"""No pass over every building may sit inside another loop.

The indexed-scans rule (docs/superpowers/specs/2026-09-13-abstraction-review.md,
section 4): a linear scan of the building collection is a phase-boundary
pass, never a per-item step. Anything that needs "the buildings with
property X" inside a loop asks the Buildings index (by_kind, by_item,
belts, at_tile, in_box, ...). The 2026-09-13 review found zero violations;
this keeps it that way.

Each violation's identity is `<path>:<enclosing function>:<unparsed iter
expression>`, not a line number: a line number shifts whenever an unrelated
edit adds or removes a line above the flagged site, which would make the
allowlist below go stale and the guard fail spuriously for reasons that have
nothing to do with a new nested scan. The expression-based key only changes
when the flagged loop itself, or the function around it, actually changes.
`<path>` is the file's path relative to the repository root, in posix form
(forward slashes), not just its basename — two modules with the same
filename in different directories must not collide on one allowlist key.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "flab2bp"

# Sites the AST heuristic flags but that are not a scan of the building
# collection. Keep this short and justify every entry.
ALLOWLIST = {
    # block.placement.buildings is one block's own placement, visited once per
    # block while packing at a gap — the "loop over blocks that scans each
    # block's own buildings" phase boundary the brief calls out, not a
    # per-item step over every building.
    "src/flab2bp/layout/hierarchy/compose.py:_pack_at:block.placement.buildings": (
        "loop over blocks, each scanning its own block's buildings once while packing"
    ),
    # failure.buildings is validate.Finding.buildings: the few building
    # indices one finding names, not the placement's building collection.
    "src/flab2bp/layout/freeform.py:_sweep:failure.buildings": (
        "Finding.buildings names a handful of buildings for one finding"
    ),
    # projected_failure.buildings is finalize.ProjectionFailure.buildings:
    # the few building indices one projected refusal names.
    "src/flab2bp/layout/routing_domain.py:_place_coaters:projected_failure.buildings": (
        "ProjectionFailure.buildings names a handful of buildings for one refusal"
    ),
    # resource.buildings is physical_flow.Resource.buildings: the few
    # building indices belonging to one capacity-model resource.
    "src/flab2bp/layout/validate.py:_piler_input_rate:resource.buildings": (
        "Resource.buildings names a handful of buildings for one resource"
    ),
}


def _scans_buildings(node: ast.For | ast.comprehension) -> bool:
    target = node.iter
    if isinstance(target, ast.Call) and isinstance(target.func, ast.Name):  # noqa: SIM102
        if target.func.id == "enumerate" and target.args:
            target = target.args[0]
    return isinstance(target, ast.Attribute) and target.attr == "buildings"


def _violations(path: Path) -> list[tuple[str, int]]:
    """Return (key, lineno) for each violation; key is line-number-free."""
    tree = ast.parse(path.read_text(), filename=str(path))
    rel = path.relative_to(ROOT).as_posix()
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, loop_depth: int, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            depth = loop_depth
            name = enclosing
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                depth = 0
                name = child.name
            elif isinstance(child, ast.Lambda):
                depth = 0
                name = "<lambda>"
            if isinstance(child, ast.For):
                if _scans_buildings(child) and loop_depth > 0:
                    key = f"{rel}:{name}:{ast.unparse(child.iter)}"
                    found.append((key, child.lineno))
                depth = loop_depth + 1
            elif isinstance(child, ast.While):
                depth = loop_depth + 1
            elif isinstance(child, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                for generator in child.generators:
                    if _scans_buildings(generator) and loop_depth > 0:
                        key = f"{rel}:{name}:{ast.unparse(generator.iter)}"
                        found.append((key, child.lineno))
                depth = loop_depth + 1
            visit(child, depth, name)

    visit(tree, 0, "<module>")
    return found


def test_no_building_scan_sits_inside_another_loop() -> None:
    violations = [v for path in sorted(SRC.rglob("*.py")) for v in _violations(path)]
    unexpected = [(key, lineno) for key, lineno in violations if key not in ALLOWLIST]
    assert unexpected == [], (
        "a pass over every building inside a loop; use the Buildings index instead: "
        + ", ".join(f"{key} (line {lineno})" for key, lineno in unexpected)
    )
