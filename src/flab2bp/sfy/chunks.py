"""Unreal's compressed chunk stream as written by FArchiveSaveCompressedProxy.

Each chunk: uint32 tag 0x9E2A83C1, int32 0x22222222, int64 max chunk size
(131072), uint8 compression method (3 = zlib), then (compressed, uncompressed)
sizes twice as int64, then the zlib bytes. Chunks repeat until end of file.
"""

from __future__ import annotations

import struct
import zlib

from flab2bp.sfy.archive import ArchiveError

__all__ = [
    "CHUNK_MARK",
    "CHUNK_TAG",
    "COMPRESSION_ZLIB",
    "MAX_CHUNK",
    "compress_stream",
    "decompress_stream",
    "find_stream_start",
]

CHUNK_TAG = 0x9E2A83C1
CHUNK_MARK = 0x22222222
MAX_CHUNK = 131072
COMPRESSION_ZLIB = 3
_HEADER = struct.Struct("<IiqBqqqq")


def find_stream_start(data: bytes, header_end: int) -> int:
    if header_end < 0 or header_end + 4 > len(data):
        raise ArchiveError(f"no room for a chunk tag at {header_end} in {len(data)} bytes")
    if struct.unpack_from("<I", data, header_end)[0] != CHUNK_TAG:
        raise ArchiveError(f"no chunk tag at {header_end}")
    return header_end


def decompress_stream(data: bytes, start: int) -> bytes:
    out: list[bytes] = []
    pos = start
    while pos < len(data):
        if pos + _HEADER.size > len(data):
            raise ArchiveError(
                f"truncated chunk header at {pos}: {len(data) - pos} bytes left, "
                f"need {_HEADER.size}"
            )
        tag, mark, maxchunk, method, c1, u1, c2, u2 = _HEADER.unpack_from(data, pos)
        if tag != CHUNK_TAG or mark != CHUNK_MARK or method != COMPRESSION_ZLIB:
            raise ArchiveError(
                f"bad chunk header at {pos}: tag={tag:#x} mark={mark:#x} method={method}"
            )
        if maxchunk != MAX_CHUNK:
            raise ArchiveError(f"chunk at {pos} declares max chunk size {maxchunk}")
        if (c1, u1) != (c2, u2):
            raise ArchiveError(f"chunk size pairs disagree at {pos}")
        if c2 < 0 or u2 < 0:
            raise ArchiveError(f"negative chunk sizes at {pos}: compressed={c2} uncompressed={u2}")
        pos += _HEADER.size
        if pos + c2 > len(data):
            raise ArchiveError(
                f"truncated chunk body at {pos}: {len(data) - pos} bytes left, need {c2}"
            )
        try:
            chunk = zlib.decompress(data[pos : pos + c2])
        except zlib.error as exc:
            raise ArchiveError(f"chunk at {pos} is not valid zlib data: {exc}") from exc
        if len(chunk) != u2:
            raise ArchiveError(f"chunk at {pos} decompressed to {len(chunk)}, declared {u2}")
        out.append(chunk)
        pos += c2
    return b"".join(out)


def compress_stream(payload: bytes, level: int = 6) -> bytes:
    out: list[bytes] = []
    for offset in range(0, len(payload), MAX_CHUNK):
        raw = payload[offset : offset + MAX_CHUNK]
        packed = zlib.compress(raw, level)
        out.append(
            _HEADER.pack(
                CHUNK_TAG,
                CHUNK_MARK,
                MAX_CHUNK,
                COMPRESSION_ZLIB,
                len(packed),
                len(raw),
                len(packed),
                len(raw),
            )
        )
        out.append(packed)
    return b"".join(out)
