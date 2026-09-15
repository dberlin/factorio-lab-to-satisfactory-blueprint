"""The bake-off: run every layout strategy over a corpus and measure them.

Both strategies get the same ``BuildSpec``, the same budget, and the same seed,
and neither is asked how it did -- the harness measures each ``Placement``
itself.  A strategy reporting its own numbers would be marking its own homework.

Why the re-exports are lazy
---------------------------
``from flab2bp.bench import measure`` used to cost the whole DSP layout stack:
the eager import of :mod:`flab2bp.bench.runner` reached ``layout.freeform``,
``routing_domain`` and the Cython kernels before the caller had asked for
anything.  That is the right price for a bake-off and the wrong one for
:mod:`flab2bp.bench.sfy_corpus`, which is a list of Satisfactory URLs and must
not drag DSP in behind it -- ``tests/sfy/test_corpus.py`` holds it to that in a
fresh interpreter.

So every name below is resolved on first access through a module ``__getattr__``
(PEP 562) and then cached in ``globals()``.  Nothing changes for a caller: the
same names import from the same place and mean the same thing, because the
``TYPE_CHECKING`` block declares them to a type checker exactly as the eager
imports did.  What changes is WHEN the submodule is read.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # the names, for a type checker, without paying for them
    from flab2bp.bench.corpus import URL_CORPUS, CorpusEntry, Tier, entries_for, entry
    from flab2bp.bench.crossvalidate import (
        CrossCheck,
        bun_available,
        crossvalidate,
        viewer_path,
    )
    from flab2bp.bench.metrics import measure
    from flab2bp.bench.regression import (
        AREA_TOLERANCE,
        Regression,
        RegressionResult,
        check_against_baseline,
        write_baseline,
    )
    from flab2bp.bench.report import (
        MatrixReport,
        matrix_report,
        render_markdown,
        write_results,
    )
    from flab2bp.bench.runner import available_strategies, run_corpus, specs_for
    from flab2bp.bench.scoring import DENSITY_DEADBAND, Verdict, compare, geometric_mean
    from flab2bp.bench.types import CellResult, Metrics

#: Which submodule each re-exported name lives in.  This is the whole of the
#: laziness: one dict, and a ``__getattr__`` that reads it.
_EXPORTS: Final[dict[str, str]] = {
    "URL_CORPUS": "flab2bp.bench.corpus",
    "CorpusEntry": "flab2bp.bench.corpus",
    "Tier": "flab2bp.bench.corpus",
    "entries_for": "flab2bp.bench.corpus",
    "entry": "flab2bp.bench.corpus",
    "CrossCheck": "flab2bp.bench.crossvalidate",
    "bun_available": "flab2bp.bench.crossvalidate",
    "crossvalidate": "flab2bp.bench.crossvalidate",
    "viewer_path": "flab2bp.bench.crossvalidate",
    "measure": "flab2bp.bench.metrics",
    "AREA_TOLERANCE": "flab2bp.bench.regression",
    "Regression": "flab2bp.bench.regression",
    "RegressionResult": "flab2bp.bench.regression",
    "check_against_baseline": "flab2bp.bench.regression",
    "write_baseline": "flab2bp.bench.regression",
    "MatrixReport": "flab2bp.bench.report",
    "matrix_report": "flab2bp.bench.report",
    "render_markdown": "flab2bp.bench.report",
    "write_results": "flab2bp.bench.report",
    "available_strategies": "flab2bp.bench.runner",
    "run_corpus": "flab2bp.bench.runner",
    "specs_for": "flab2bp.bench.runner",
    "DENSITY_DEADBAND": "flab2bp.bench.scoring",
    "Verdict": "flab2bp.bench.scoring",
    "compare": "flab2bp.bench.scoring",
    "geometric_mean": "flab2bp.bench.scoring",
    "CellResult": "flab2bp.bench.types",
    "Metrics": "flab2bp.bench.types",
}

#: Written out rather than computed from ``_EXPORTS`` so that a linter can see
#: the ``TYPE_CHECKING`` imports above are re-exports and not dead code.  The
#: two cannot drift: ``__dir__`` reports ``_EXPORTS`` and
#: ``tests/sfy/test_corpus.py`` compares it with this list.
__all__ = [
    "AREA_TOLERANCE",
    "DENSITY_DEADBAND",
    "CellResult",
    "CorpusEntry",
    "CrossCheck",
    "MatrixReport",
    "Metrics",
    "Regression",
    "RegressionResult",
    "Tier",
    "URL_CORPUS",
    "Verdict",
    "available_strategies",
    "bun_available",
    "check_against_baseline",
    "compare",
    "crossvalidate",
    "entries_for",
    "entry",
    "geometric_mean",
    "matrix_report",
    "measure",
    "render_markdown",
    "run_corpus",
    "specs_for",
    "viewer_path",
    "write_baseline",
    "write_results",
]


def __getattr__(name: str) -> Any:
    """Resolve a re-export on first access, then cache it in ``globals()``."""
    where = _EXPORTS.get(name)
    if where is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(where), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Everything ``__getattr__`` can resolve, which is the real export list."""
    return sorted(_EXPORTS)
