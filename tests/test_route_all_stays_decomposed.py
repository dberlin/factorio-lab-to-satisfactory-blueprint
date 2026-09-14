"""`_route_all` is a driver over `_RouteAllRun`, and stays one.

The 2026-09-13 abstraction review (section 9 item 3) found `_route_all` at
3,729 lines with 67 nested functions and 20 `nonlocal` statements over 23
rebound names. Plan C lifted them onto `_RouteAllRun` as fields and methods:
`_route_all` is now the prologue plus the round loop, with zero nested
functions and zero lambdas, and every rebound name is a typed field.

Three rules:

1. no nested `def`, `async def` or `lambda` inside `_route_all` itself --
   a new one is a new closure over run state, which is what Plan C removed;
2. no `nonlocal` anywhere in `routing_domain`, except the allowlisted sites
   below -- rebound state belongs on a typed field, where a sibling method
   can see it and mypy can check it;
3. `_route_all` stays a driver, measured in lines.

Scope, deliberately narrow: rule 1 bans nested functions inside `_route_all`
itself, and does NOT ban nested functions inside `_RouteAllRun`'s methods --
six of them are load-bearing, and `_search_route`'s `admit_source_family` is
created conditionally so that `admit_proposal` is `None` when no sibling is
unrouted. Lifting that one would change behavior. Do not widen rule 1 to the
whole module or the whole class.

A violation's identity is ``<path>:<enclosing function>``, not a line number:
a line number shifts whenever an unrelated edit adds or removes a line above
the site, which would make the allowlist below go stale and the guard fail
for reasons that have nothing to do with a new closure. ``<path>`` is the
file's path relative to the repository root, in posix form (forward slashes),
not just its basename -- two modules with the same filename in different
directories must not collide on one allowlist key. ``<enclosing function>``
is the dotted chain of function and class names around the site.

Plan C's other two abstraction pins, the request objects that replaced
`_merge_frontier`'s 18 parameters (14 keyword-only, now the 14 fields of
`_MergeFrontierRequest`) and `_pack_window`'s 17 parameters (16
keyword-only, now the 16 fields of `_PackWindowRequest`), are already
enforced next to the code they describe and are deliberately not
duplicated here: `tests/layout/test_route_witnesses.py`'s
`test_merge_frontier_takes_five_parameters` and
`tests/layout/test_freeform.py`'s
`test_pack_window_takes_a_request_built_at_each_call_site`.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTING_DOMAIN = ROOT / "src" / "flab2bp" / "layout" / "routing_domain.py"

#: The maximum size of `_route_all` as a driver. It was 3,729 lines before
#: Plan C and is 806 after; the bound is the measured figure rounded up to the
#: next hundred, so an unrelated edit has room and a re-inlined cluster does
#: not. Never raise this to a number the tree does not already meet.
DRIVER_LINE_BOUND = 900

#: `nonlocal` statements that are not run state escaping `_route_all`. Keep
#: this short and justify every entry.
NONLOCAL_ALLOWLIST = {
    # A three-line local search inside one power-infill site chooser, whose
    # rebound name never leaves that function and never touches routing run
    # state. Unrelated to `_route_all` and to `_RouteAllRun`.
    "src/flab2bp/layout/routing_domain.py:plan_power_infill.select_site": (
        "one local best-so-far inside a self-contained site chooser"
    ),
}


def _tree() -> ast.Module:
    return ast.parse(ROUTING_DOMAIN.read_text(), filename=str(ROUTING_DOMAIN))


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


def _route_all_node(tree: ast.Module) -> ast.FunctionDef:
    return next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "_route_all"
    )


def _nonlocal_sites(tree: ast.Module) -> list[tuple[str, int, str]]:
    scopes = _scopes(tree)
    return [
        (
            f"{_relative(ROUTING_DOMAIN)}:{scopes[id(node)]}",
            node.lineno,
            ", ".join(node.names),
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Nonlocal)
    ]


def _render(sites: list[tuple[str, int, str]]) -> str:
    return "; ".join(f"{key} (line {lineno}): {detail}" for key, lineno, detail in sites)


def test_route_all_holds_no_closures() -> None:
    node = _route_all_node(_tree())
    offenders = [
        f"{child.lineno}: {getattr(child, 'name', '<lambda>')}"
        for child in ast.walk(node)
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
        and child is not node
    ]
    assert offenders == [], "put it on _RouteAllRun as a method instead: " + ", ".join(offenders)


def test_routing_domain_declares_no_nonlocal_name() -> None:
    offenders = [site for site in _nonlocal_sites(_tree()) if site[0] not in NONLOCAL_ALLOWLIST]
    assert offenders == [], "carry rebound state on a typed field, not a nonlocal: " + _render(
        offenders
    )


def test_every_allowlisted_nonlocal_still_exists() -> None:
    """A stale allowlist entry is a rule nobody is enforcing any more."""
    declared = {site[0] for site in _nonlocal_sites(_tree())}
    assert sorted(NONLOCAL_ALLOWLIST) == sorted(declared), (
        "NONLOCAL_ALLOWLIST drifted from the tree; remove what moved away and justify what arrived"
    )


def test_route_all_is_a_driver() -> None:
    node = _route_all_node(_tree())
    assert node.end_lineno is not None
    measured = node.end_lineno - node.lineno + 1
    assert measured < DRIVER_LINE_BOUND, (
        f"_route_all is {measured} lines; it was 3,729 and should now be the "
        "prologue plus the round loop"
    )
