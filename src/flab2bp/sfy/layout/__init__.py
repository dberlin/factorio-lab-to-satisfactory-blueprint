"""Placing a Satisfactory build: where objects stand and what shape a belt is.

The package is layered the way the stages run. :mod:`~flab2bp.sfy.layout.splines`
is arithmetic alone -- the shapes the belt hologram's own router builds, with no
model and no file in sight. :mod:`~flab2bp.sfy.layout.model` says where every
object stands, in centimetres, and nothing more: it judges nothing and refuses
nothing. :mod:`~flab2bp.sfy.layout.emit` turns one of those placements into a
blueprint by cloning the objects the game itself wrote, and reads it back.
:mod:`~flab2bp.sfy.layout.validate` judges the result.

What is legal is not decided by any of the first three. The validator decides
it, and it decides nothing on its own authority: every check it runs names the
rule in ``data/hologram_rules.json`` it enforces, or declares that the bound is
this project's own and says why. See ``docs/sfy-layout-model.md``.
"""

from __future__ import annotations
