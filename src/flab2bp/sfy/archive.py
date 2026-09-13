"""Little-endian Unreal archive primitives used by every Satisfactory blueprint layer.

Byte identity is the contract: whatever ``Reader`` consumes, ``Writer`` must
reproduce. The one encoding choice the game makes, ANSI versus UTF-16 for
strings, follows Unreal's rule (wide iff any code point is above 0x7F), so it
is derived from the value rather than stored.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, ClassVar

__all__ = ["ArchiveError", "ObjectRef", "Reader", "Writer", "is_wide"]


class ArchiveError(ValueError):
    """Malformed or unexpected bytes."""


def is_wide(s: str) -> bool:
    return any(ord(ch) > 0x7F for ch in s)


@dataclass(frozen=True, slots=True)
class ObjectRef:
    level: str
    path: str
    NULL: ClassVar[ObjectRef]

    @property
    def is_null(self) -> bool:
        return self.level == "" and self.path == ""

    @property
    def name(self) -> str:
        """Last dotted component, e.g. ``Build_ConstructorMk1_C_1``."""
        return self.path.rsplit(".", 1)[-1]


ObjectRef.NULL = ObjectRef("", "")


class Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def _unpack(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        if self.pos + size > len(self.data):
            raise ArchiveError(f"need {size} bytes at {self.pos}, have {self.remaining()}")
        value = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += size
        return value

    def i8(self) -> int:
        return self._unpack("<b")

    def u8(self) -> int:
        return self._unpack("<B")

    def u16(self) -> int:
        return self._unpack("<H")

    def i32(self) -> int:
        return self._unpack("<i")

    def u32(self) -> int:
        return self._unpack("<I")

    def i64(self) -> int:
        return self._unpack("<q")

    def f32(self) -> float:
        return self._unpack("<f")

    def f64(self) -> float:
        return self._unpack("<d")

    def bytes(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ArchiveError(f"need {n} bytes at {self.pos}, have {self.remaining()}")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def guid(self) -> bytes:
        return self.bytes(16)

    def fstring(self) -> str:
        n = self.i32()
        if n == 0:
            return ""
        if n < 0:
            raw = self.bytes(-2 * n)
            if raw[-2:] != b"\x00\x00":
                raise ArchiveError(f"wide string at {self.pos} lacks NUL")
            return raw[:-2].decode("utf-16-le")
        raw = self.bytes(n)
        if raw[-1:] != b"\x00":
            raise ArchiveError(f"ansi string at {self.pos} lacks NUL")
        return raw[:-1].decode("latin-1")

    def object_ref(self) -> ObjectRef:
        return ObjectRef(self.fstring(), self.fstring())

    def expect_end(self) -> None:
        if self.pos != len(self.data):
            raise ArchiveError(f"{self.remaining()} unread bytes at {self.pos}")


class Writer:
    __slots__ = ("_parts", "_size")

    def __init__(self) -> None:
        self._parts: list[bytes] = []
        self._size = 0

    def _pack(self, fmt: str, value: int | float) -> None:
        self.raw(struct.pack(fmt, value))

    def raw(self, b: bytes) -> None:
        self._parts.append(b)
        self._size += len(b)

    def i8(self, v: int) -> None:
        self._pack("<b", v)

    def u8(self, v: int) -> None:
        self._pack("<B", v)

    def u16(self, v: int) -> None:
        self._pack("<H", v)

    def i32(self, v: int) -> None:
        self._pack("<i", v)

    def u32(self, v: int) -> None:
        self._pack("<I", v)

    def i64(self, v: int) -> None:
        self._pack("<q", v)

    def f32(self, v: float) -> None:
        self._pack("<f", v)

    def f64(self, v: float) -> None:
        self._pack("<d", v)

    def guid(self, b: bytes) -> None:
        if len(b) != 16:
            raise ArchiveError(f"guid must be 16 bytes, got {len(b)}")
        self.raw(b)

    def fstring(self, s: str) -> None:
        if s == "":
            self.i32(0)
        elif is_wide(s):
            encoded = s.encode("utf-16-le") + b"\x00\x00"
            self.i32(-(len(encoded) // 2))
            self.raw(encoded)
        else:
            encoded = s.encode("latin-1") + b"\x00"
            self.i32(len(encoded))
            self.raw(encoded)

    def object_ref(self, ref: ObjectRef) -> None:
        self.fstring(ref.level)
        self.fstring(ref.path)

    def getvalue(self) -> bytes:
        return b"".join(self._parts)

    def __len__(self) -> int:
        return self._size
