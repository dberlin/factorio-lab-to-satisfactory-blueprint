from __future__ import annotations

from collections import Counter

import pytest

from flab2bp.sfy.archive import ArchiveError, Reader, Writer
from flab2bp.sfy.codec import read_sbp
from flab2bp.sfy.objects import read_toc
from tests.sfy.conftest import FIXTURES


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
