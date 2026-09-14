"""Whole-file reader and writer for .sbp and .sbpcfg.

A .sbp file is the blueprint header followed by the compressed chunk stream.
The decompressed payload is int32 total size, then the object table (int32
size, int32 count, that many TOC entries) and the object blobs (int32 size,
int32 count, then int32 size plus that many bytes per object).

The header also decides how the object bodies are laid out: :func:`tag_format`
turns it into a :class:`~flab2bp.sfy.objects.TagFormat`, computed once per file
and handed to every object read and write, so nothing downstream has to guess
the tag format from the bytes it is about to parse.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flab2bp.sfy.archive import ArchiveError, Reader, Writer
from flab2bp.sfy.chunks import compress_stream, decompress_stream, find_stream_start
from flab2bp.sfy.header import (
    BlueprintHeader,
    BlueprintRecord,
    read_header,
    read_record,
    write_header,
    write_record,
)
from flab2bp.sfy.objects import (
    ObjectData,
    ObjectHeader,
    TagFormat,
    read_object_data,
    read_toc,
    write_object_data,
    write_toc,
)
from flab2bp.sfy.versions import MODERN_TAG_UE5_VERSION

__all__ = [
    "Blueprint",
    "payload_bytes",
    "read_pair",
    "read_sbp",
    "read_sbp_file",
    "read_sbpcfg",
    "tag_format",
    "write_sbp",
    "write_sbp_file",
    "write_sbpcfg",
]

read_sbpcfg = read_record
write_sbpcfg = write_record


@dataclass(frozen=True, slots=True)
class Blueprint:
    header: BlueprintHeader
    objects: tuple[tuple[ObjectHeader, ObjectData], ...]


def tag_format(header: BlueprintHeader) -> TagFormat:
    """Derive the object-body layout from the file header.

    A blueprint old enough to carry no engine-version block predates both the
    UE 5.4 property tag and the serialization-control byte; otherwise its
    FileVersionUE5 says whether it has them.
    """
    if header.version_data is None:
        return TagFormat(modern=False, control_byte=False)
    modern = header.version_data.ue5 >= MODERN_TAG_UE5_VERSION
    return TagFormat(modern=modern, control_byte=modern)


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
    fmt = tag_format(header)
    objects = []
    for h in headers:
        size = p.i32()
        objects.append((h, read_object_data(p.bytes(size), h, fmt)))
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
    fmt = tag_format(bp.header)
    for h, d in bp.objects:
        raw = write_object_data(d, h, fmt)
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


def read_sbp_file(path: Path) -> Blueprint:
    """Read a .sbp file from disk."""
    return read_sbp(Path(path).read_bytes())


def write_sbp_file(path: Path, bp: Blueprint, zlib_level: int = 6) -> None:
    """Write a blueprint to a .sbp file."""
    Path(path).write_bytes(write_sbp(bp, zlib_level))


def read_pair(sbp_path: Path, cfg_path: Path) -> tuple[Blueprint, BlueprintRecord]:
    """Read the two files the game writes for one blueprint: .sbp and .sbpcfg."""
    return read_sbp_file(sbp_path), read_sbpcfg(Path(cfg_path).read_bytes())
