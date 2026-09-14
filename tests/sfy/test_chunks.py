import struct

import pytest

from flab2bp.sfy.archive import ArchiveError, Reader
from flab2bp.sfy.chunks import (
    CHUNK_TAG,
    MAX_CHUNK,
    compress_stream,
    decompress_stream,
    find_stream_start,
)
from flab2bp.sfy.header import read_header
from tests.sfy.conftest import FIXTURES


def _payload_and_stream(path):
    data = path.read_bytes()
    r = Reader(data)
    read_header(r)
    start = find_stream_start(data, r.pos)
    return decompress_stream(data, start), data[start:]


def test_chunk_layout_of_first_chunk(sbp_path):
    data = sbp_path.read_bytes()
    r = Reader(data)
    read_header(r)
    start = find_stream_start(data, r.pos)
    tag, mark, maxchunk, method, c1, u1, c2, u2 = struct.unpack_from("<IiqBqqqq", data, start)
    assert tag == CHUNK_TAG and mark == 0x22222222 and maxchunk == MAX_CHUNK and method == 3
    assert c1 == c2 and u1 == u2 and u2 <= MAX_CHUNK


def test_decompressed_payload_declares_its_own_length(sbp_path):
    payload, _ = _payload_and_stream(sbp_path)
    assert struct.unpack_from("<i", payload)[0] == len(payload) - 4


def test_recompression_reproduces_payload(sbp_path):
    payload, _ = _payload_and_stream(sbp_path)
    again = compress_stream(payload)
    assert decompress_stream(again, 0) == payload


def test_recompression_is_byte_identical_at_level_6(sbp_path):
    """The game's zlib output equals Python's at level 6 for every fixture,
    which pins the compressed bytes the writer emits, not just the payload."""
    payload, stream = _payload_and_stream(sbp_path)
    assert compress_stream(payload, level=6) == stream


def _walk_chunk_headers(stream):
    """Yield each chunk header's (tag, compressed size, uncompressed size)."""
    pos = 0
    while pos < len(stream):
        tag, _mark, _maxchunk, _method, _c1, _u1, c2, u2 = struct.unpack_from(
            "<IiqBqqqq", stream, pos
        )
        yield tag, c2, u2
        pos += struct.calcsize("<IiqBqqqq") + c2


def test_multi_chunk_payload_round_trips():
    payload = bytes(range(256)) * 2000  # 512000 bytes -> 4 chunks
    stream = compress_stream(payload)
    chunks = list(_walk_chunk_headers(stream))
    assert [tag for tag, _, _ in chunks] == [CHUNK_TAG] * 4
    assert [u for _, _, u in chunks] == [
        MAX_CHUNK,
        MAX_CHUNK,
        MAX_CHUNK,
        len(payload) - 3 * MAX_CHUNK,
    ]
    assert decompress_stream(stream, 0) == payload


def _biofuel_stream():
    data = (FIXTURES / "biofuel.sbp").read_bytes()
    r = Reader(data)
    read_header(r)
    return data[find_stream_start(data, r.pos) :]


def _mutated(offset, raw):
    stream = bytearray(_biofuel_stream())
    stream[offset : offset + len(raw)] = raw
    return bytes(stream)


def test_bad_tag_raises_archive_error():
    stream = _mutated(0, b"\x00")
    with pytest.raises(ArchiveError, match="bad chunk header"):
        decompress_stream(stream, 0)


def test_max_chunk_size_mismatch_raises_archive_error():
    stream = _mutated(8, struct.pack("<q", MAX_CHUNK + 1))
    with pytest.raises(ArchiveError, match="max chunk size"):
        decompress_stream(stream, 0)


def test_size_pair_mismatch_raises_archive_error():
    first = struct.unpack_from("<q", _biofuel_stream(), 33)[0]
    stream = _mutated(33, struct.pack("<q", first + 1))
    with pytest.raises(ArchiveError, match="size pairs disagree"):
        decompress_stream(stream, 0)


def test_declared_uncompressed_length_mismatch_raises_archive_error():
    wrong = struct.pack("<q", struct.unpack_from("<q", _biofuel_stream(), 41)[0] - 1)
    stream = bytearray(_biofuel_stream())
    stream[25:33] = wrong  # u1
    stream[41:49] = wrong  # u2, so the pair check still passes
    with pytest.raises(ArchiveError, match="decompressed to"):
        decompress_stream(bytes(stream), 0)


def test_negative_compressed_size_raises_archive_error():
    stream = bytearray(_biofuel_stream())
    stream[17:25] = struct.pack("<q", -1)  # c1
    stream[33:41] = struct.pack("<q", -1)  # c2, so the pair check still passes
    with pytest.raises(ArchiveError, match="negative chunk sizes"):
        decompress_stream(bytes(stream), 0)


def test_truncated_chunk_header_raises_archive_error():
    with pytest.raises(ArchiveError, match="truncated chunk header"):
        decompress_stream(_biofuel_stream() + b"\x00" * 10, 0)


def test_truncated_chunk_body_raises_archive_error():
    with pytest.raises(ArchiveError, match="truncated chunk body"):
        decompress_stream(_biofuel_stream()[:-5], 0)


def test_corrupt_zlib_body_raises_archive_error():
    stream = _mutated(49, b"\xff\xff")
    with pytest.raises(ArchiveError, match="not valid zlib data"):
        decompress_stream(stream, 0)


def test_find_stream_start_rejects_an_offset_without_a_tag():
    data = (FIXTURES / "biofuel.sbp").read_bytes()
    r = Reader(data)
    read_header(r)
    with pytest.raises(ArchiveError, match="no chunk tag"):
        find_stream_start(data, r.pos + 1)


def test_find_stream_start_rejects_an_offset_past_the_end():
    data = (FIXTURES / "biofuel.sbp").read_bytes()
    with pytest.raises(ArchiveError, match="no room for a chunk tag"):
        find_stream_start(data, len(data) - 2)
