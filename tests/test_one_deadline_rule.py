"""One module owns the deadline and the charged-work ledger.

The 2026-09-13 abstraction review (section 5, P1) found the concept spread
over five named predicates, three closures, eight inline comparisons, three
exception types and an untyped ``budget["left"]`` dict at 42 sites. Plan B
collapsed them into ``flab2bp.layout.budget``. This keeps it collapsed.

Four rules, one test each:

1. only ``budget.py`` compares a clock reading to a deadline -- everyone else
   calls ``budget.expired(deadline, time.monotonic)``, which is the only place
   the ``None`` never expires convention and the ``>=`` live;
2. no module carries a work ledger as a ``{"left": ...}`` dict;
3. only ``routing_domain._routing_pass_budget`` and the handful of carve sites
   that hand a child its own slice construct a ``WorkBudget(left=...)``;
4. only that same factory reads the ``_ROUTING_BUDGET`` floor, so the three
   callers cannot disagree about its value the way they latently did.

A violation's identity is ``<path>:<enclosing function>``, not a line number:
a line number shifts whenever an unrelated edit adds or removes a line above
the site, which would make the allowlists below go stale and the guard fail
for reasons that have nothing to do with a new deadline or a new ledger.
``<path>`` is the file's path relative to the repository root, in posix form
(forward slashes), not just its basename -- two modules with the same
filename in different directories must not collide on one allowlist key.
``<enclosing function>`` is the dotted chain of function and class names
around the site, because every routing carve site sits in a closure nested
inside ``_route_all``, and a bare ``_route_all`` key could not tell them apart.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "flab2bp"
OWNER = SRC / "layout" / "budget.py"
FACTORY = "src/flab2bp/layout/routing_domain.py:_routing_pass_budget"

#: Sites that hand a child search its own slice of the parent's ledger and
#: reconcile the unspent remainder back afterwards. Each one is a deliberate
#: carve, not a fresh budget out of nowhere; the factory is the only place a
#: pass-sized ledger is born. Keep this short and justify every entry.
CARVE_SITES = {
    FACTORY: "the factory itself: the one place a routing pass's ledger is seeded",
    "src/flab2bp/layout/routing_domain.py:_route_all": (
        "the coverage pass's per-net allowance, aliased to the shared ledger otherwise"
    ),
    "src/flab2bp/layout/routing_domain.py:_route_all._search.probe_ordinary": (
        "an ordinary-query probe's private slice of what the pass has left"
    ),
    "src/flab2bp/layout/routing_domain.py:_route_all._repair._grouped_overcap_alternative": (
        "a grouped over-cap alternative's slice, capped at the parent's remainder"
    ),
    "src/flab2bp/layout/routing_domain.py:_route_all._cluster_search": (
        "one cluster search's private allowance, reconciled against the parent"
    ),
    "src/flab2bp/layout/sequence_solver.py:_route_detailed_candidate": (
        "one detailed candidate's routing attempt, charged back to the staged ledger"
    ),
}

#: Deadline comparisons that are not a deadline comparison. Empty on purpose:
#: an elapsed-time measurement (``time.monotonic() - started > limit``) already
#: fails the clock test below because its left operand is a ``BinOp``.
COMPARISON_ALLOWLIST: dict[str, str] = {}


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _scopes(tree: ast.AST) -> dict[int, str]:
    """Every node id mapped to the dotted function/class chain around it."""
    chains: dict[int, str] = {}

    def walk(node: ast.AST, chain: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = chain
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = (*chain, child.name)
            chains[id(child)] = ".".join(inner) or "<module>"
            walk(child, inner)

    chains[id(tree)] = "<module>"
    walk(tree, ())
    return chains


def _is_clock_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"monotonic", "perf_counter"}
    )


def _deadline_comparisons(path: Path, tree: ast.Module) -> list[tuple[str, int, str]]:
    """Clock readings compared against a bound, in either direction.

    ``>=`` and ``>`` are the shapes the review counted; ``<`` and ``<=`` are
    the same predicate written backwards (``a < b`` is ``not a >= b``) and one
    survived in ``hierarchy/strategy.py`` until Plan B's last task, so they
    count too.
    """
    scopes = _scopes(tree)
    found: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, ast.GtE | ast.Gt | ast.Lt | ast.LtE) for op in node.ops):
            continue
        if not (_is_clock_call(node.left) or any(_is_clock_call(c) for c in node.comparators)):
            continue
        found.append((f"{_relative(path)}:{scopes[id(node)]}", node.lineno, ast.unparse(node)))
    return found


def _left_dicts(path: Path, tree: ast.Module) -> list[tuple[str, int, str]]:
    scopes = _scopes(tree)
    found: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            if not any(isinstance(key, ast.Constant) and key.value == "left" for key in node.keys):
                continue
        elif isinstance(node, ast.Subscript):
            if not (isinstance(node.slice, ast.Constant) and node.slice.value == "left"):
                continue
        else:
            continue
        found.append((f"{_relative(path)}:{scopes[id(node)]}", node.lineno, ast.unparse(node)))
    return found


def _work_budget_seeds(path: Path, tree: ast.Module) -> list[tuple[str, int, str]]:
    scopes = _scopes(tree)
    found: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "WorkBudget":
            continue
        if not any(keyword.arg == "left" for keyword in node.keywords):
            continue
        found.append((f"{_relative(path)}:{scopes[id(node)]}", node.lineno, ast.unparse(node)))
    return found


def _routing_floor_reads(path: Path, tree: ast.Module) -> list[tuple[str, int, str]]:
    """Reads of the ``_ROUTING_BUDGET`` floor outside the factory body.

    An ``ast.Attribute`` read is another module reaching through the module
    object; an ``ast.Name`` load is ``routing_domain`` reading its own global.
    The assignment that defines it is a ``Store`` and is not a read.
    """
    scopes = _scopes(tree)
    found: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr != "_ROUTING_BUDGET":
                continue
        elif isinstance(node, ast.Name):
            if node.id != "_ROUTING_BUDGET" or not isinstance(node.ctx, ast.Load):
                continue
        else:
            continue
        found.append((f"{_relative(path)}:{scopes[id(node)]}", node.lineno, ast.unparse(node)))
    return found


def _modules() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(), filename=str(path)))
        for path in sorted(SRC.rglob("*.py"))
    ]


def _render(sites: list[tuple[str, int, str]]) -> str:
    return ", ".join(f"{key} (line {lineno}: {source})" for key, lineno, source in sites)


def test_only_the_budget_module_compares_a_clock_to_a_deadline() -> None:
    offenders = [
        site
        for path, tree in _modules()
        if path != OWNER
        for site in _deadline_comparisons(path, tree)
        if site[0] not in COMPARISON_ALLOWLIST
    ]
    assert offenders == [], (
        "call flab2bp.layout.budget.expired(deadline, time.monotonic) instead: "
        + _render(offenders)
    )


def test_no_module_carries_a_work_ledger_as_a_left_dict() -> None:
    offenders = [site for path, tree in _modules() for site in _left_dicts(path, tree)]
    assert offenders == [], "carry a flab2bp.layout.budget.WorkBudget instead: " + _render(
        offenders
    )


def test_only_the_factory_and_the_carve_sites_seed_a_work_budget() -> None:
    offenders = [
        site
        for path, tree in _modules()
        for site in _work_budget_seeds(path, tree)
        if site[0] not in CARVE_SITES
    ]
    assert offenders == [], (
        "seed a pass ledger through routing_domain._routing_pass_budget, or add the "
        "carve site to CARVE_SITES with its reason: " + _render(offenders)
    )


def test_every_carve_site_still_exists() -> None:
    """A stale allowlist entry is a rule nobody is enforcing any more."""
    seeded = {site[0] for path, tree in _modules() for site in _work_budget_seeds(path, tree)}
    assert sorted(CARVE_SITES) == sorted(seeded), (
        "CARVE_SITES drifted from the tree; remove what moved away and justify what arrived"
    )


def test_only_the_factory_reads_the_routing_budget_floor() -> None:
    offenders = [
        site
        for path, tree in _modules()
        for site in _routing_floor_reads(path, tree)
        if site[0] != FACTORY
    ]
    assert offenders == [], (
        "call routing_domain._routing_pass_budget() so every caller sees one floor: "
        + _render(offenders)
    )
