from __future__ import annotations

import pytest

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer, is_wide


def test_fstring_ansi_round_trip() -> None:
    w = Writer()
    w.fstring("Persistent_Level")
    assert w.getvalue() == b"\x11\x00\x00\x00Persistent_Level\x00"
    assert Reader(w.getvalue()).fstring() == "Persistent_Level"


def test_fstring_empty_is_zero_length() -> None:
    w = Writer()
    w.fstring("")
    assert w.getvalue() == b"\x00\x00\x00\x00"
    assert Reader(w.getvalue()).fstring() == ""


def test_fstring_wide_uses_negative_utf16_length() -> None:
    w = Writer()
    w.fstring("Ärger")
    data = w.getvalue()
    assert data[:4] == (-6).to_bytes(4, "little", signed=True)
    assert data[4:] == "Ärger".encode("utf-16-le") + b"\x00\x00"
    assert Reader(data).fstring() == "Ärger"
    assert is_wide("Ärger") and not is_wide("plain")


def test_a_high_byte_in_an_ansi_string_is_refused() -> None:
    """Unreal writes ANSI only for code points <= 0x7F.

    Decoding such a byte as latin-1 would hand back a code point above 0x7F,
    :func:`is_wide` would flip, and the writer would put the same string back as
    UTF-16 with a different length word -- byte identity lost, silently. No
    fixture carries one, so the reader has to say so itself.
    """
    with pytest.raises(ArchiveError, match="non-ASCII byte in an ANSI string at 4"):
        Reader(b"\x02\x00\x00\x00\xe9\x00").fstring()


def test_a_string_the_writer_cannot_encode_is_refused() -> None:
    """The write-side mirror: whatever goes out is ASCII or UTF-16, or it raises."""
    with pytest.raises(ArchiveError, match="cannot go into an archive"):
        Writer().fstring("\ud800")


def test_a_non_ascii_string_goes_out_wide() -> None:
    w = Writer()
    w.fstring("café")
    assert w.getvalue()[:4] == (-5).to_bytes(4, "little", signed=True)
    assert Reader(w.getvalue()).fstring() == "café"


def test_scalars_round_trip() -> None:
    w = Writer()
    w.i32(-5)
    w.u32(7)
    w.i64(-(1 << 40))
    w.f32(1.5)
    w.f64(2.25)
    w.u8(255)
    w.i8(-1)
    w.u16(65535)
    r = Reader(w.getvalue())
    assert (r.i32(), r.u32(), r.i64(), r.f32(), r.f64(), r.u8(), r.i8(), r.u16()) == (
        -5,
        7,
        -(1 << 40),
        1.5,
        2.25,
        255,
        -1,
        65535,
    )
    r.expect_end()


def test_object_ref_round_trip_and_null() -> None:
    ref = ObjectRef(
        "Persistent_Level",
        "Persistent_Level:PersistentLevel.Build_ConstructorMk1_C_1",
    )
    w = Writer()
    w.object_ref(ref)
    w.object_ref(ObjectRef.NULL)
    r = Reader(w.getvalue())
    assert r.object_ref() == ref
    assert r.object_ref().is_null


def test_expect_end_raises_on_leftover() -> None:
    with pytest.raises(ArchiveError):
        Reader(b"\x00\x00").expect_end()


def test_guid_is_16_bytes() -> None:
    w = Writer()
    w.guid(bytes(range(16)))
    assert Reader(w.getvalue()).guid() == bytes(range(16))
    with pytest.raises(ArchiveError):
        Writer().guid(b"short")
