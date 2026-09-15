"""Which FactorioLab dataset a request is about.

A leaf module on purpose: :mod:`flab2bp.lab.params` decodes a URL's params and
:mod:`flab2bp.lab.url` builds the request on top of it, so the enum they both
need cannot live in either without one importing the other. Nothing here
imports anything else from this package.

:class:`Game` is re-exported from :mod:`flab2bp.lab.url`, which is where it used
to live, so ``from flab2bp.lab.url import Game`` keeps working.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Game"]


class Game(StrEnum):
    """A FactorioLab dataset this tool builds blueprints for.

    The value is the first path segment of a FactorioLab URL and the directory
    name under ``data/`` that serves that dataset's ``data.json``/``hash.json``.
    """

    DSP = "dsp"
    SFY = "sfy"
