from __future__ import annotations

import pytest

from flab2bp.sfy.archive import ArchiveError, Reader, Writer
from flab2bp.sfy.chunks import compress_stream, decompress_stream, find_stream_start
from flab2bp.sfy.codec import payload_bytes, read_sbp, write_sbp
from flab2bp.sfy.header import read_header, write_header
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


def test_payload_size_mismatch_raises_archive_error():
    data = (FIXTURES / "biofuel.sbp").read_bytes()
    r = Reader(data)
    header = read_header(r)
    w = Writer()
    write_header(w, header)
    w.raw(compress_stream(b"\x00\x00\x00\x00junk"))
    with pytest.raises(ArchiveError):
        read_sbp(w.getvalue())
