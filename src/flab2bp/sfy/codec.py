"""Whole-file reader and writer for .sbp and .sbpcfg.

A .sbp file is the blueprint header followed by the compressed chunk stream.
The decompressed payload is int32 total size, then the object table (int32
size, int32 count, that many TOC entries) and the object blobs (int32 size,
int32 count, then int32 size plus that many bytes per object).
"""

from __future__ import annotations

from dataclasses import dataclass

from flab2bp.sfy.archive import ArchiveError, Reader, Writer
from flab2bp.sfy.chunks import compress_stream, decompress_stream, find_stream_start
from flab2bp.sfy.header import (
    BlueprintHeader,
    read_header,
    read_record,
    write_header,
    write_record,
)
from flab2bp.sfy.objects import (
    ObjectData,
    ObjectHeader,
    read_object_data,
    read_toc,
    write_object_data,
    write_toc,
)

__all__ = ["Blueprint", "payload_bytes", "read_sbp", "read_sbpcfg", "write_sbp", "write_sbpcfg"]

read_sbpcfg = read_record
write_sbpcfg = write_record


@dataclass(frozen=True, slots=True)
class Blueprint:
    header: BlueprintHeader
    objects: tuple[tuple[ObjectHeader, ObjectData], ...]


def read_sbp(data: bytes) -> Blueprint:
    r = Reader(data)
    header = read_header(r)
    payload = decompress_stream(data, find_stream_start(data, r.pos))
    p = Reader(payload)
    total = p.i32()
    if total != len(payload) - 4:
        raise ArchiveError(f"payload declares {total}, has {len(payload) - 4}")
    toc_size = p.i32()
    toc_start = p.pos
    count = p.i32()
    headers = read_toc(p, count, header.save_version)
    if p.pos - toc_start != toc_size:
        raise ArchiveError(f"toc consumed {p.pos - toc_start}, declared {toc_size}")
    blob_size = p.i32()
    blob_start = p.pos
    blob_count = p.i32()
    if blob_count != count:
        raise ArchiveError(f"{blob_count} blobs for {count} objects")
    objects = []
    for h in headers:
        size = p.i32()
        objects.append((h, read_object_data(p.bytes(size), h)))
    if p.pos - blob_start != blob_size:
        raise ArchiveError(f"blob consumed {p.pos - blob_start}, declared {blob_size}")
    p.expect_end()
    return Blueprint(header, tuple(objects))


def payload_bytes(bp: Blueprint) -> bytes:
    toc = Writer()
    toc.i32(len(bp.objects))
    write_toc(toc, tuple(h for h, _ in bp.objects), bp.header.save_version)
    blob = Writer()
    blob.i32(len(bp.objects))
    for h, d in bp.objects:
        raw = write_object_data(d, h)
        blob.i32(len(raw))
        blob.raw(raw)
    body = Writer()
    body.i32(len(toc))
    body.raw(toc.getvalue())
    body.i32(len(blob))
    body.raw(blob.getvalue())
    out = Writer()
    out.i32(len(body))
    out.raw(body.getvalue())
    return out.getvalue()


def write_sbp(bp: Blueprint, zlib_level: int = 6) -> bytes:
    w = Writer()
    write_header(w, bp.header)
    w.raw(compress_stream(payload_bytes(bp), zlib_level))
    return w.getvalue()
