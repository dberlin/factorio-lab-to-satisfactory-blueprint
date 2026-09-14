"""Read the connection directions the game's own C++ constructors set.

A cooked asset omits a component template's property whenever it equals the
**archetype**'s value, and for a connection component the archetype is a native
constructor the pak does not carry. So ``tools/sfy-extract`` sees no
``mDirection`` on 84 of the 288 connection templates in the content, and the
value those ports really carry is in the shipped DLL's machine code.

This writes ``src/flab2bp/sfy/data/native_directions.json``, the file
``tools/sfy-extract`` reads to finish the job. Run it after ``tools/sfy-native``
and before the extractor::

    uv run python scripts/sfy_native_directions.py

**Nothing here is inferred from a port's name or from a blueprint corpus.**
Every value is a store (or a proven absence of one) in a constructor the tool
disassembles, quoted by address, so a game update that moves a constructor stops
the run rather than shipping a stale answer.

Two kinds of default come out of it:

``component_defaults``
    what a connection component carries when nobody sets it -- the class default
    object's value, from the component class's own constructor. This is the
    archetype at the root of every chain.

``owner_defaults``
    a buildable whose native constructor *creates* its connections and sets them
    itself, which overrides the component class default for every Blueprint
    deriving from it. ``AFGBuildableConveyorBase`` is the only one: it sets both
    of its ends to ``FCD_ANY``, which is what ``ConveyorAny0``/``ConveyorAny1``
    are named after.

``AFGBuildableConveyorBase``'s constructor is one MSVC split into chained
``.pdata`` chunks, so the store that sets its *second* connection is 600 bytes
past the chunk the symbol is in. ``sfy-native disasm`` follows the chain, and
both stores are quoted from its output.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flab2bp.sfy import docs

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "flab2bp" / "sfy" / "data"
NATIVE_TOOL = ROOT / "tools" / "sfy-native"

WIN64 = "FactoryGame/Binaries/Win64"
MODULE = "FactoryGameEGS-FactoryGame-Win64-Shipping"

# The two enums, in declaration order, mapped onto the registry's vocabulary.
# ``Source/FactoryGame/Public/FGFactoryConnectionComponent.h:27`` and
# ``Source/FactoryGame/Public/FGPipeConnectionComponent.h:20``; both are
# ``: uint8`` with no explicit values, so the ordinal is the index.
ENUMS: dict[str, tuple[str, ...]] = {
    # FCD_INPUT, FCD_OUTPUT, FCD_ANY, FCD_SNAP_ONLY
    "EFactoryConnectionDirection": ("input", "output", "any", "snap_only"),
    # PCT_ANY, PCT_CONSUMER, PCT_PRODUCER, PCT_SNAP_ONLY
    "EPipeConnectionType": ("any", "input", "output", "snap_only"),
}

ENUM_NAMES: dict[str, tuple[str, ...]] = {
    "EFactoryConnectionDirection": ("FCD_INPUT", "FCD_OUTPUT", "FCD_ANY", "FCD_SNAP_ONLY"),
    "EPipeConnectionType": ("PCT_ANY", "PCT_CONSUMER", "PCT_PRODUCER", "PCT_SNAP_ONLY"),
}

# The class whose constructor states the default, per cooked component class
# name (which is what a template's ``Class`` is called in the pak, without the
# U/A prefix the C++ name carries).
#
# ``member`` is the property the direction lives in, and ``offset`` is checked
# against the one the tool annotates, never assumed. A ``None`` member means the
# class has no direction property at all, which is the case for a power
# connection: it is a circuit connection, and ``EFactoryConnectionDirection``
# is a ``UFGFactoryConnectionComponent`` member.
COMPONENTS: dict[str, dict[str, Any]] = {
    "FGFactoryConnectionComponent": {
        "class": "UFGFactoryConnectionComponent",
        "member": "mDirection",
        "enum": "EFactoryConnectionDirection",
        # The constructor stores the enum's zero outright.
        "evidence": ("0x7b5ff1",),
        "note": (
            "UFGFactoryConnectionComponent's constructor stores FCD_INPUT. A machine's "
            "belt port that says nothing is an input because this is what its archetype "
            "carries, not because of what the component is called."
        ),
    },
    "FGPipeConnectionComponent": {
        "class": "UFGPipeConnectionComponent",
        "member": "mPipeConnectionType",
        "enum": "EPipeConnectionType",
        # No store: the member keeps UObject's zero-initialisation, PCT_ANY.
        "constructors": (
            "UFGPipeConnectionComponentBase::UFGPipeConnectionComponentBase",
            "UFGPipeConnectionComponent::UFGPipeConnectionComponent",
        ),
        "note": (
            "Neither UFGPipeConnectionComponentBase's constructor nor "
            "UFGPipeConnectionComponent's writes mPipeConnectionType, so it keeps the "
            "zero UObject construction leaves, which is PCT_ANY."
        ),
    },
    "FGPipeConnectionFactory": {
        "class": "UFGPipeConnectionFactory",
        "member": "mPipeConnectionType",
        "enum": "EPipeConnectionType",
        "constructors": (
            "UFGPipeConnectionComponentBase::UFGPipeConnectionComponentBase",
            "UFGPipeConnectionComponent::UFGPipeConnectionComponent",
            "UFGPipeConnectionFactory::UFGPipeConnectionFactory",
        ),
        "note": (
            "UFGPipeConnectionFactory derives from UFGPipeConnectionComponent (its "
            "constructor calls it) and no constructor in the chain writes "
            "mPipeConnectionType, so it too is PCT_ANY."
        ),
    },
    "FGPowerConnectionComponent": {
        "class": "UFGPowerConnectionComponent",
        "member": None,
        "enum": None,
        "constructors": ("UFGPowerConnectionComponent::UFGPowerConnectionComponent",),
        "note": (
            "A power connection is a circuit connection: its constructor chains to "
            "UFGCircuitConnectionComponent, not to UFGFactoryConnectionComponent, and "
            "the class carries no direction property. 'any' here says there is no "
            "direction to take, not that one was read."
        ),
    },
}

# A buildable whose own native constructor creates its connections and sets
# their direction. That subobject is then the archetype of the template every
# Blueprint deriving from the class carries, so it -- not the component class
# default -- is what an omitted property means there.
#
# There are exactly four in the content, one per family of buildable whose
# connection templates are outered to the class default object rather than being
# Blueprint components: belts and lifts, conveyor poles, pipelines and pipeline
# supports. ``owners`` are the classes a cooked Blueprint chain actually ends at
# and each is held to the binary -- its constructor must be seen calling
# ``setter``'s, or the run stops.
#
# ``store`` is the instruction that writes the direction. It is an immediate
# except on the pipeline, where MSVC stores the low byte of a register; ``zeroed``
# names where that register was cleared, and the run checks nothing writes it in
# between. (It is ``rbp``, callee-saved under the x64 ABI, so the calls between
# do not disturb it.)
OWNERS: tuple[dict[str, Any], ...] = (
    {
        "setter": "AFGBuildableConveyorBase::AFGBuildableConveyorBase",
        "component_class": "FGFactoryConnectionComponent",
        "enum": "EFactoryConnectionDirection",
        "owners": ("AFGBuildableConveyorBelt", "AFGBuildableConveyorLift"),
        # Both stores, each with the load that names the connection it writes.
        # The second pair is in a chained .pdata chunk, which sfy-native follows.
        "evidence": ("0x4c98ec", "0x4c9902", "0x4c9914", "0x4c999d", "0x4c9b47", "0x4c9b59"),
        "store": "0x4c9914",
        "also_store": ("0x4c9b59",),
        "note": (
            "AFGBuildableConveyorBase's constructor creates both ends and sets both to "
            "FCD_ANY, which is what ConveyorAny0 and ConveyorAny1 are named after. "
            "FGBuildableConveyorBase.h:380 -- 'mConnection0 is the input, mConnection1 "
            "is the output' -- is about which end items enter and leave by; it is not "
            "mDirection. A placed belt takes its two directions from whatever it "
            "snapped to, under the belt.snap_directions rule."
        ),
    },
    {
        "setter": "AFGBuildablePoleConveyor::AFGBuildablePoleConveyor",
        "component_class": "FGFactoryConnectionComponent",
        "enum": "EFactoryConnectionDirection",
        "owners": ("AFGBuildablePoleConveyor",),
        "evidence": ("0x51b567", "0x51b5bf"),
        "store": "0x51b5bf",
        "note": (
            "A conveyor pole's one connection is FCD_SNAP_ONLY: it is something to snap "
            "a belt to, not something that takes or gives items. Build_ConveyorPole_C "
            "inherits it; the stackable and wall poles are Blueprint components and "
            "spell the same value out themselves."
        ),
    },
    {
        "setter": "AFGBuildablePipeline::AFGBuildablePipeline",
        "component_class": "FGPipeConnectionComponent",
        "enum": "EPipeConnectionType",
        "owners": ("AFGBuildablePipeline",),
        "evidence": (
            "0x51ac2c", "0x51ae87", "0x51ae9c", "0x51aeab",
            "0x51af26", "0x51af3b", "0x51af4a",
        ),
        "store": "0x51aeab",
        "zeroed": "0x51ac2c",
        "also_store": ("0x51af4a",),
        "note": (
            "Both ends of a pipeline are PCT_ANY: fluid runs either way along one. The "
            "value is stored from bpl, which xor ebp,ebp cleared at the top of the "
            "constructor and nothing writes again before either store."
        ),
    },
    {
        "setter": "AFGBuildablePolePipe::AFGBuildablePolePipe",
        "component_class": "FGPipeConnectionComponent",
        "enum": "EPipeConnectionType",
        "owners": ("AFGBuildablePolePipe",),
        "evidence": ("0x666d23", "0x666d75"),
        "store": "0x666d75",
        "note": (
            "A pipeline support's connection is PCT_SNAP_ONLY, the pipe's counterpart "
            "to the conveyor pole's FCD_SNAP_ONLY."
        ),
    },
)

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _disasm(dll: Path, pdb: Path, symbol: str, out: Path) -> list[dict[str, Any]]:
    """Run ``sfy-native disasm`` for one symbol and return its function records."""
    subprocess.run(
        [
            "cargo", "run", "--release", "--quiet", "--",
            str(dll), str(pdb), "disasm", symbol, "--out", str(out),
        ],
        cwd=NATIVE_TOOL,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return [f for f in json.loads(out.read_text(encoding="utf-8")) if f["symbol"] == symbol]


def _line(instruction: dict[str, Any]) -> str:
    """One evidence line: the tool's own text, plus whatever it annotated."""
    text = f"{instruction['rva']}: {instruction['text']}"
    member = instruction.get("member")
    if member:
        return f"{text}  ; {member['class']}::{member['name']} @{member['offset']}"
    call = instruction.get("call")
    if call:
        return f"{text}  ; -> {call}"
    return text


def _function_of(functions: list[dict[str, Any]], instruction: dict[str, Any]) -> dict[str, Any]:
    """The disassembled function an instruction came out of."""
    return next(
        f for f in functions if instruction["rva"] in {i["rva"] for i in f["instructions"]}
    )


def _base_calls(function: dict[str, Any]) -> list[dict[str, Any]]:
    """The calls to another class's constructor: the inheritance, as the tool saw it."""
    own = function["symbol"].split("::")[0]
    return [
        i
        for i in function["instructions"]
        if "::" in i.get("call", "") and i["call"].split("::")[0] != own
    ]


def _by_rva(functions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["rva"]: i for f in functions for i in f["instructions"]}


def _pick(functions: list[dict[str, Any]], rvas: tuple[str, ...], what: str) -> dict[str, Any]:
    """The instructions at ``rvas``, or stop naming the ones that moved."""
    found = _by_rva(functions)
    missing = [rva for rva in rvas if rva not in found]
    if missing:
        raise SystemExit(
            f"{what}: no instruction at {missing} -- the constructor moved, and the "
            "directions have to be re-read before this file can be written"
        )
    return found


def _stores_at(functions: list[dict[str, Any]], offset: int) -> list[str]:
    """Every instruction that writes through ``[reg+offset]``."""
    pattern = re.compile(rf"\[\w+\+{offset:X}h\]\s*,")
    return [
        f"{f['symbol']} {i['rva']}: {i['text']}"
        for f in functions
        for i in f["instructions"]
        if pattern.search(i["text"])
    ]


def main(out: Path | None = None) -> int:
    """Write ``data/native_directions.json`` from the installed game's binary."""
    install = docs.satisfactory_dir()
    win64 = install / WIN64
    dll, pdb = win64 / f"{MODULE}.dll", win64 / f"{MODULE}.pdb"
    for path in (dll, pdb):
        if not path.is_file():
            raise SystemExit(f"no {path}: this needs the game install, its DLL and its PDB")

    native = json.loads((DATA / "native.json").read_text(encoding="utf-8"))["provenance"]
    dll_sha256 = _sha256(dll)
    if dll_sha256 != native["dll_sha256"]:
        raise SystemExit(
            "the DLL has moved since native.json was written "
            f"({dll_sha256} vs {native['dll_sha256']}): re-run tools/sfy-native first"
        )

    scratch = DATA / ".native_directions_disasm.json"
    cache: dict[str, list[dict[str, Any]]] = {}

    def run(symbol: str) -> list[dict[str, Any]]:
        if symbol not in cache:
            cache[symbol] = _disasm(dll, pdb, symbol, scratch)
            if not cache[symbol]:
                raise SystemExit(f"the PDB names no {symbol}")
        return cache[symbol]

    try:
        components, offset = _components(run)
        owners = [_owner(rule, run, offset) for rule in OWNERS]
    finally:
        scratch.unlink(missing_ok=True)

    payload = {
        "provenance": {
            "dll": dll.name,
            "dll_sha256": dll_sha256,
            "pdb_guid": native["pdb_guid"],
            "extracted": datetime.now(UTC).date().isoformat(),
            "tool": native["tool"],
            "method": (
                "sfy-native disasm, one run per constructor. Every direction is a store "
                "the tool decoded, or a proven absence of one, quoted by address. The "
                "blueprint corpus and the component's name are not inputs and are never "
                "evidence for a direction."
            ),
        },
        "enums": {name: list(values) for name, values in sorted(ENUMS.items())},
        "component_defaults": components,
        "owner_defaults": owners,
    }
    target = DATA / "native_directions.json" if out is None else Path(out)
    target.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for entry in components:
        print(f"{entry['component_class']:<30} {entry['direction']:<10} {entry['class']}")
    for owner in owners:
        print(
            f"{owner['component_class']:<30} {owner['direction']:<10} "
            f"{owner['set_in']} for {', '.join(owner['owner_classes'])}"
        )
    print(f"-> {target}")
    return 0


def _components(run) -> tuple[list[dict[str, Any]], int]:
    """Every connection component class's own default, and mDirection's offset."""
    factory = COMPONENTS["FGFactoryConnectionComponent"]
    functions = run(f"{factory['class']}::{factory['class']}")
    store = _pick(functions, factory["evidence"], factory["class"])[factory["evidence"][0]]
    member = store.get("member")
    if not member or member["name"] != factory["member"]:
        raise SystemExit(
            f"{factory['evidence'][0]} is no longer a {factory['member']} store: "
            f"{store['text']}"
        )
    value = _immediate(store["text"])
    offset = int(member["offset"])
    entries = [
        {
            "component_class": "FGFactoryConnectionComponent",
            "class": factory["class"],
            "member": factory["member"],
            "offset": offset,
            "enum": factory["enum"],
            "value": value,
            "enum_name": ENUM_NAMES[factory["enum"]][value],
            "direction": ENUMS[factory["enum"]][value],
            "function": f"{factory['class']}::{factory['class']}",
            "rva": _function_of(functions, store)["rva"],
            "instructions": [_line(store)],
            "note": factory["note"],
        }
    ]
    for name, spec in COMPONENTS.items():
        if name == "FGFactoryConnectionComponent":
            continue
        functions = [f for symbol in spec["constructors"] for f in run(symbol)]
        wrote = _stores_at(functions, offset)
        if wrote:
            raise SystemExit(
                f"{name}: a constructor now writes at offset {offset}, so the "
                f"zero-initialised default is no longer the answer:\n  " + "\n  ".join(wrote)
            )
        entries.append(
            {
                "component_class": name,
                "class": spec["class"],
                "member": spec["member"],
                "offset": offset if spec["member"] else None,
                "enum": spec["enum"],
                "value": 0 if spec["member"] else None,
                "enum_name": ENUM_NAMES[spec["enum"]][0] if spec["enum"] else None,
                "direction": ENUMS[spec["enum"]][0] if spec["enum"] else "any",
                "function": spec["constructors"][-1],
                "rva": functions[-1]["rva"],
                "instructions": [_line(i) for f in functions for i in _base_calls(f)],
                "note": spec["note"],
            }
        )
    return entries, offset


def _immediate(text: str) -> int:
    """The immediate a ``mov byte ptr [reg+disp],N`` stores."""
    match = re.search(r",\s*([0-9A-Fa-f]+)h?$", text)
    if match is None:
        raise SystemExit(f"cannot read the stored value out of {text!r}")
    raw = match.group(1)
    return int(raw, 16 if text.rstrip().endswith("h") else 10)


def _owner(rule: dict[str, Any], run, offset: int) -> dict[str, Any]:
    """One native buildable's own default, and the classes it reaches."""
    setter = rule["setter"]
    functions = run(setter)
    found = _pick(functions, rule["evidence"], setter)
    stores = [found[rva] for rva in (rule["store"], *rule.get("also_store", ()))]
    for one in stores:
        if f"+{offset:X}h]" not in one["text"]:
            raise SystemExit(f"{one['rva']} no longer writes at offset {offset}: {one['text']}")
    value = _stored_value(rule, found, stores)

    entry: dict[str, Any] = {
        "component_class": rule["component_class"],
        "owner_classes": _owners(rule, run),
        "enum": rule["enum"],
        "value": value,
        "enum_name": ENUM_NAMES[rule["enum"]][value],
        "direction": ENUMS[rule["enum"]][value],
        "set_in": setter,
        "rva": _function_of(functions, stores[0])["rva"],
        "instructions": [_line(found[rva]) for rva in rule["evidence"]],
        "inheritance": _inheritance(rule, run),
        "note": rule["note"],
    }
    return entry


def _stored_value(rule: dict[str, Any], found: dict[str, Any], stores: list[dict[str, Any]]) -> int:
    """What the stores write: an immediate, or a register something cleared.

    Every store in a rule has to write the same value -- a constructor that set
    one of a buildable's two connections one way and the other another way would
    need a rule per connection, and the run stops rather than reporting the
    first.
    """
    if "zeroed" not in rule:
        values = {_immediate(one["text"]) for one in stores}
        if len(values) != 1:
            raise SystemExit(f"{rule['setter']} no longer stores one value: {sorted(values)}")
        return values.pop()
    cleared = found[rule["zeroed"]]
    register = re.fullmatch(r"xor (\w+),\1", cleared["text"])
    if register is None:
        raise SystemExit(f"{rule['zeroed']} is no longer a register clear: {cleared['text']}")
    family = _family(register.group(1))
    for one in stores:
        source = one["text"].rsplit(",", 1)[1].strip()
        if _family(source) != family:
            raise SystemExit(f"{one['rva']} no longer stores from {family}: {one['text']}")
        for between in _between(found, cleared["rva"], one["rva"]):
            if _family(_destination(between["text"])) == family:
                raise SystemExit(
                    f"{between['rva']} writes {family} between the clear at "
                    f"{rule['zeroed']} and the store at {one['rva']}: {between['text']}"
                )
    return 0


# The register names that are the same machine register, so a store from the low
# byte of one that ``xor`` cleared is a store of zero.
_FAMILIES = {
    frozenset(("rax", "eax", "ax", "al")),
    frozenset(("rbx", "ebx", "bx", "bl")),
    frozenset(("rcx", "ecx", "cx", "cl")),
    frozenset(("rdx", "edx", "dx", "dl")),
    frozenset(("rsi", "esi", "si", "sil")),
    frozenset(("rdi", "edi", "di", "dil")),
    frozenset(("rbp", "ebp", "bp", "bpl")),
}


def _family(register: str) -> str | None:
    for family in _FAMILIES:
        if register in family:
            return min(family)
    return None


def _destination(text: str) -> str:
    return text.split(None, 1)[1].split(",")[0].strip() if " " in text else ""


def _between(found: dict[str, Any], first: str, last: str) -> list[dict[str, Any]]:
    low, high = int(first, 16), int(last, 16)
    return [i for rva, i in found.items() if low < int(rva, 16) < high]


def _owners(rule: dict[str, Any], run) -> list[str]:
    """The classes this default reaches, each held to the binary."""
    _inheritance(rule, run)
    return list(rule["owners"])


def _inheritance(rule: dict[str, Any], run) -> list[str]:
    """Why each owner class carries ``setter``'s default: its constructor calls it."""
    setter = rule["setter"]
    owning_class = setter.split("::")[0]
    lines = []
    for owner in rule["owners"]:
        if owner == owning_class:
            lines.append(f"{owner}: {setter} is its own constructor")
            continue
        calls = [
            _line(i)
            for f in run(f"{owner}::{owner}")
            for i in f["instructions"]
            if i.get("call") == setter
        ]
        if not calls:
            raise SystemExit(
                f"{owner}'s constructor is not seen calling {setter}, so it cannot be "
                "claimed to inherit that default"
            )
        lines.append(f"{owner}: {calls[0]}")
    return lines


if __name__ == "__main__":
    sys.exit(main())
