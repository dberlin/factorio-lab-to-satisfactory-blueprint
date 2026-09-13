from __future__ import annotations

from collections import Counter

import pytest

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer
from flab2bp.sfy.codec import read_sbp
from flab2bp.sfy.objects import (
    ObjectData,
    ObjectHeader,
    Transform,
    read_object_data,
    read_toc,
    write_object_data,
)
from flab2bp.sfy.trailers import BuildableTrailer, ComponentTrailer
from tests.sfy.conftest import FIXTURES

TERMINATOR = b"\x05\x00\x00\x00None\x00"


def _component_header(name: str) -> ObjectHeader:
    return ObjectHeader(
        0,
        f"/Game/FactoryGame/{name}.{name}",
        "Persistent_Level",
        f"Persistent_Level:PersistentLevel.Build_ConveyorBeltMk1_C_1.{name}0",
        0,
        None,
        None,
        None,
        "Persistent_Level:PersistentLevel.Build_ConveyorBeltMk1_C_1",
    )


def _actor_header(name: str) -> ObjectHeader:
    return ObjectHeader(
        1,
        f"/Game/FactoryGame/{name}.{name}_C",
        "Persistent_Level",
        f"Persistent_Level:PersistentLevel.{name}_C_1",
        0,
        Transform((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
        0,
        0,
        None,
    )


def test_biofuel_object_table():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    kinds = Counter(h.class_path.rsplit(".", 1)[-1] for h, _ in bp.objects)
    assert len(bp.objects) == 194
    assert kinds["Build_Foundation_Concrete_8x1_C"] == 48
    assert kinds["Build_ConstructorMk1_C"] == 3
    assert kinds["FGFactoryConnectionComponent"] == 46
    first = bp.objects[0][0]
    assert first.kind == 1 and first.transform.translation == (1200.0, -1200.0, 50.0)
    assert first.transform.scale == (1.0, 1.0, 1.0)


def test_components_name_their_parent():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    actors = {h.path for h, _ in bp.objects if h.kind == 1}
    for h, d in bp.objects:
        if h.kind == 0:
            assert h.parent in actors, h.path
            assert d.parent is None and d.components is None
        else:
            assert d.parent is not None and d.components is not None


def test_actor_component_lists_match_the_table():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    by_parent = Counter(h.parent for h, _ in bp.objects if h.kind == 0)
    for h, d in bp.objects:
        if h.kind == 1:
            assert len(d.components) == by_parent.get(h.path, 0), h.path


def test_body_with_a_control_byte_round_trips_through_object_data():
    """The serialization-control byte lives on ``ObjectData``, in front of the properties."""
    h = _component_header("FGFactoryConnectionComponent")
    d = ObjectData(None, None, 0, (), ComponentTrailer())
    raw = write_object_data(d, h)
    assert raw == b"\x00" + TERMINATOR + bytes(8)
    assert read_object_data(raw, h) == d


def test_body_without_a_control_byte_round_trips_through_object_data():
    """The older saves have no such byte, and ``control`` is None rather than 0."""
    h = _actor_header("Build_ConstructorMk1")
    d = ObjectData(ObjectRef.NULL, (), None, (), BuildableTrailer())
    raw = write_object_data(d, h)
    assert raw.endswith(TERMINATOR + bytes(4))
    assert read_object_data(raw, h) == d


def test_unknown_object_kind_is_rejected():
    w = Writer()
    w.i32(7)
    with pytest.raises(ArchiveError):
        read_toc(Reader(w.getvalue()), 1, 60)


def test_truncated_toc_entry_raises_archive_error():
    w = Writer()
    w.i32(1)
    w.fstring("/Game/FactoryGame/Whatever.Whatever_C")
    with pytest.raises(ArchiveError):
        read_toc(Reader(w.getvalue()), 1, 60)
