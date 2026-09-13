from __future__ import annotations

import pytest

from flab2bp.sfy.archive import ArchiveError, Reader, Writer
from flab2bp.sfy.chunks import compress_stream, decompress_stream, find_stream_start
from flab2bp.sfy.codec import payload_bytes, read_sbp, tag_format, write_sbp
from flab2bp.sfy.header import read_header, write_header
from flab2bp.sfy.objects import TagFormat
from tests.sfy.conftest import FIXTURES


def test_payload_round_trip_is_byte_identical(sbp_path):
    data = sbp_path.read_bytes()
    r = Reader(data)
    read_header(r)
    original_payload = decompress_stream(data, find_stream_start(data, r.pos))
    bp = read_sbp(data)
    assert payload_bytes(bp) == original_payload


def test_header_bytes_round_trip(sbp_path):
    data = sbp_path.read_bytes()
    r = Reader(data)
    read_header(r)
    assert write_sbp(read_sbp(data))[: r.pos] == data[: r.pos]


def test_full_file_round_trip_is_byte_identical(sbp_path):
    data = sbp_path.read_bytes()
    assert write_sbp(read_sbp(data)) == data


def test_tag_format_comes_from_the_header_not_the_body():
    """No version block means the classic tag; UE5 1017 means the 5.4 tag and a control byte."""
    classic = read_sbp((FIXTURES / "production-3.sbp").read_bytes()).header
    modern = read_sbp((FIXTURES / "biofuel.sbp").read_bytes()).header
    assert classic.save_version == 52 and classic.version_data is None
    assert modern.save_version == 60 and modern.version_data.ue5 == 1017
    assert tag_format(classic) == TagFormat(modern=False, control_byte=False)
    assert tag_format(modern) == TagFormat(modern=True, control_byte=True)


def test_control_byte_is_present_exactly_when_the_header_says_modern(sbp_path):
    bp = read_sbp(sbp_path.read_bytes())
    expected = tag_format(bp.header).control_byte
    assert all((d.control is not None) == expected for _, d in bp.objects)


def test_payload_size_mismatch_raises_archive_error():
    data = (FIXTURES / "biofuel.sbp").read_bytes()
    r = Reader(data)
    header = read_header(r)
    w = Writer()
    write_header(w, header)
    w.raw(compress_stream(b"\x00\x00\x00\x00junk"))
    with pytest.raises(ArchiveError):
        read_sbp(w.getvalue())
