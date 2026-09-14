"""Driving ``sfy-native disasm``, and what may be claimed from what it returns.

Both ``scripts/sfy_native_directions.py`` and ``scripts/sfy_native_rules.py``
read the game's behaviour out of the shipped DLL by disassembling named
functions, and both make claims of the form *this function does not do X*: no
constructor in the pipe-connection chain writes ``mPipeConnectionType``, so it
keeps ``PCT_ANY``; nothing in ``AFGConveyorLiftHologram``'s validators quantises
a height to ``mStepHeight``, so the effect of that rule is ``none``.

**An absence is only as good as the bound on the function it was looked for
in.** ``sfy-native`` reports that bound as ``size_source``, from three sources
in order of how well the game states them:

``pdata``                 the ``RUNTIME_FUNCTION`` entry covering the symbol
``pdata-chained``         that entry plus every chunk MSVC chained onto it
``pdb-procedure-length``  the length the PDB's ``S_GPROC32``/``S_LPROC32``
                          record states, for a leaf with no ``.pdata`` entry
``ret``                   neither knew the function, so the decoder walked to
                          the first ``ret`` -- which may be the first of several
``truncated``             the range was cut short by the section or the 64 KiB
                          cap, whichever source asked for it

The first three are the whole function. The last two are *part* of a function,
and a store, a comparison or a quantisation may sit in the part that was never
decoded -- so :data:`BOUNDED_SIZE_SOURCES` is the set an absence may be claimed
from, and :func:`require_bounded` is what refuses the claim otherwise.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "BOUNDED_SIZE_SOURCES",
    "NATIVE_TOOL",
    "disasm",
    "require_bounded",
    "unbounded",
]

ROOT = Path(__file__).resolve().parent.parent
NATIVE_TOOL = ROOT / "tools" / "sfy-native"

#: The ``size_source`` values that mean the whole function was read. Anything
#: else means the decoder stopped somewhere the game does not say the function
#: does, and nothing may be concluded from what was *not* seen past that point.
BOUNDED_SIZE_SOURCES = ("pdata", "pdata-chained", "pdb-procedure-length")


def disasm(dll: Path, pdb: Path, symbol: str, out: Path) -> list[dict[str, Any]]:
    """Run ``sfy-native disasm`` for one symbol and return its function records."""
    subprocess.run(
        [
            "cargo", "run", "--release", "--quiet", "--",
            str(dll), str(pdb), "disasm", symbol, "--out", str(out),
        ],
        cwd=NATIVE_TOOL,
        check=True,
        stdout=subprocess.DEVNULL,
    )  # fmt: skip
    return json.loads(out.read_text(encoding="utf-8"))


def unbounded(functions: Iterable[Mapping[str, Any]]) -> list[str]:
    """Every function of ``functions`` that was not read to an end the game states.

    Each is reported as ``Class::Method @ 0xrva: size_source <source>``. A
    record with no ``size_source`` at all counts as unbounded: an older tool
    wrote it, and what it did with the end of the function is not on record.
    """
    return [
        f"{function.get('symbol', '?')} @ {function.get('rva', '?')}: "
        f"size_source {function.get('size_source', 'absent')}"
        for function in functions
        if function.get("size_source") not in BOUNDED_SIZE_SOURCES
    ]


def require_bounded(functions: Iterable[Mapping[str, Any]], claim: str) -> None:
    """Stop the run unless every function in ``functions`` was read to its end.

    ``claim`` is the absence about to be asserted, in words, so the failure says
    what could not be established rather than only what went wrong.
    """
    short = unbounded(functions)
    if short:
        raise SystemExit(
            f"{claim}: that is an absence, and it cannot be claimed from a function "
            f"sfy-native could not read to the end of:\n  " + "\n  ".join(short) + "\n"
            f"A size_source outside {list(BOUNDED_SIZE_SOURCES)} means the decoder "
            "stopped early, so what is not in the disassembly may still be in the "
            "function. Re-read it before this file can be written."
        )
