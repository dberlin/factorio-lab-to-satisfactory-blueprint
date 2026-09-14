from __future__ import annotations

from collections import Counter

import pytest

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer
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

WIRE = "Build_PowerLine_C"
POLE = ObjectRef("Persistent_Level", "Persistent_Level:PersistentLevel.Pole_1.PowerConnection")
MACHINE = ObjectRef(
    "Persistent_Level", "Persistent_Level:PersistentLevel.Smelter_1.PowerConnection"
)


def _encoded(trailer: PowerLineTrailer) -> bytes:
    w = Writer()
    write_trailer(w, trailer)
    return w.getvalue()


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


def test_a_new_power_line_is_authored_from_its_two_connection_references():
    assert trailer_for_new(WIRE, 1, (POLE, MACHINE)) == PowerLineTrailer((POLE, MACHINE))


def test_a_new_power_line_without_connection_references_is_refused():
    """A wire is the one class whose trailer cannot be filled in from the class name."""
    with pytest.raises(KeyError):
        trailer_for_new(WIRE, 1)


def test_connection_references_are_refused_for_a_class_that_has_none():
    with pytest.raises(ArchiveError):
        trailer_for_new("Build_ConstructorMk1_C", 1, (POLE, MACHINE))


def test_a_synthetic_wire_trailer_round_trips_through_its_own_named_fields():
    trailer = PowerLineTrailer((POLE, MACHINE))
    raw = _encoded(trailer)
    r = Reader(raw)
    assert read_trailer(r, WIRE, 1) == trailer
    r.expect_end()


def test_a_wire_trailer_starts_with_the_four_zero_bytes_every_actor_writes():
    """The lead ``int32`` is ``UObject::Serialize``'s, not the wire's own."""
    assert _encoded(PowerLineTrailer((POLE, MACHINE))).startswith(b"\x00\x00\x00\x00")


def test_a_dangling_wire_keeps_its_two_null_references():
    trailer = PowerLineTrailer((ObjectRef.NULL, ObjectRef.NULL))
    assert _encoded(trailer) == b"\x00" * 20
    assert read_trailer(Reader(b"\x00" * 20), WIRE, 1) == trailer


def test_every_power_line_in_the_corpus_decodes_into_named_connection_references():
    """All 508 wires the 49 fixtures carry name two circuit connections in the level."""
    wires = []
    for path in fixture_paths():
        for h, d in read_sbp(path.read_bytes()).objects:
            if h.class_name == WIRE:
                assert isinstance(d.trailer, PowerLineTrailer)
                wires.append(d.trailer)
    assert len(wires) == 508
    for trailer in wires:
        assert len(trailer.connections) == 2
        for ref in trailer.connections:
            assert ref.level == "Persistent_Level"
            assert ref.path.startswith("Persistent_Level:PersistentLevel.")


def test_every_power_line_trailer_in_the_corpus_re_encodes_byte_identically():
    """The decode is lossless per wire, not merely lossless once the file is reassembled."""
    checked = 0
    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        for h, d in bp.objects:
            if h.class_name != WIRE:
                continue
            assert isinstance(d.trailer, PowerLineTrailer)
            raw = _encoded(d.trailer)
            r = Reader(raw)
            assert read_trailer(r, WIRE, 1) == d.trailer
            r.expect_end()
            checked += 1
    assert checked == 508


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (b"", "no bytes at all"),
        (b"\x00\x00\x00", "a truncated lead int32"),
        (b"\x01\x00\x00\x00" + b"\x00" * 16, "a lead int32 the engine never writes"),
        (b"\x00" * 16, "only three of the four strings"),
        (b"\x00" * 24, "four bytes left over after the second reference"),
        (b"\x00\x00\x00\x00\x05\x00\x00\x00ab", "a length longer than the bytes that follow"),
        (b"\x00\x00\x00\x00\x02\x00\x00\x00ab" + b"\x00" * 12, "a string with no NUL"),
    ],
)
def test_malformed_wire_trailer_bytes_raise(raw, why):
    with pytest.raises(ArchiveError):
        read_trailer(Reader(raw), WIRE, 1)


def test_each_trailer_shape_round_trips():
    for class_name, kind, expected in (
        ("Build_ConstructorMk1_C", 1, BuildableTrailer()),
        ("FGFactoryConnectionComponent", 0, ComponentTrailer()),
        ("Build_ConveyorBeltMk1_C", 1, ConveyorTrailer(())),
        ("Build_PowerLine_C", 1, PowerLineTrailer((POLE, MACHINE))),
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
