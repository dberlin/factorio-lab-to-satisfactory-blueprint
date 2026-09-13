"""No pass over every building may sit inside another loop.

The indexed-scans rule (docs/superpowers/specs/2026-09-13-abstraction-review.md,
section 4): a linear scan of the building collection is a phase-boundary
pass, never a per-item step. Anything that needs "the buildings with
property X" inside a loop asks the Buildings index (by_kind, by_item,
belts, at_tile, in_box, ...). The 2026-09-13 review found zero violations;
this keeps it that way.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "flab2bp"

# Sites the AST heuristic flags but that are not a scan of the building
# collection. Keep this short and justify every entry.
ALLOWLIST = {
    # block.placement.buildings is one block's own placement, visited once per
    # block while packing at a gap — the "loop over blocks that scans each
    # block's own buildings" phase boundary the brief calls out, not a
    # per-item step over every building.
    "compose.py:914": (
        "loop over blocks, each scanning its own block's buildings once while packing"
    ),
    # failure.buildings is validate.Finding.buildings: the few building
    # indices one finding names, not the placement's building collection.
    "freeform.py:6584": "Finding.buildings names a handful of buildings for one finding",
    # projected_failure.buildings is finalize.ProjectionFailure.buildings:
    # the few building indices one projected refusal names.
    "routing_domain.py:18040": (
        "ProjectionFailure.buildings names a handful of buildings for one refusal"
    ),
    # resource.buildings is physical_flow.Resource.buildings: the few
    # building indices belonging to one capacity-model resource.
    "validate.py:6022": "Resource.buildings names a handful of buildings for one resource",
}


def _scans_buildings(node: ast.For | ast.comprehension) -> bool:
    target = node.iter
    if isinstance(target, ast.Call) and isinstance(target.func, ast.Name):  # noqa: SIM102
        if target.func.id == "enumerate" and target.args:
            target = target.args[0]
    return isinstance(target, ast.Attribute) and target.attr == "buildings"


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []

    def visit(node: ast.AST, loop_depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            depth = loop_depth
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                depth = 0
            if isinstance(child, ast.For):
                if _scans_buildings(child) and loop_depth > 0:
                    found.append(f"{path.name}:{child.lineno}")
                depth = loop_depth + 1
            elif isinstance(child, ast.While):
                depth = loop_depth + 1
            elif isinstance(child, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                for generator in child.generators:
                    if _scans_buildings(generator) and loop_depth > 0:
                        found.append(f"{path.name}:{child.lineno}")
                depth = loop_depth + 1
            visit(child, depth)

    visit(tree, 0)
    return found


def test_no_building_scan_sits_inside_another_loop() -> None:
    violations = [v for path in sorted(SRC.rglob("*.py")) for v in _violations(path)]
    unexpected = [v for v in violations if v not in ALLOWLIST]
    assert unexpected == [], (
        "a pass over every building inside a loop; use the Buildings index instead: "
        + ", ".join(unexpected)
    )
