"""Cut pressure: the register-pressure analogue for a recipe DAG.

For a boundary between two sets of machines:

* ``item_pressure`` -- how many distinct items are produced on one side and
  consumed on the other. The number of *nets* that must cross.
* ``lane_pressure`` -- ``sum(ceil(rate / lane capacity))`` over those items. The
  number of *belts* that must cross, which is the thing the geometry actually
  pays for: an item at 4x the belt rate costs four lanes, not one net.

Lane capacity is ``spec.lane_capacity`` (the fastest belt the save allows) times
the planning stack, so a stacked bus is priced at what it really carries.

The second half of this module cuts by DEPTH instead of by size: the recipe DAG
is levelled, the pressure of every depth boundary is computed, and blocks are
formed between the boundaries where pressure is locally minimal -- keeping a
high-pressure region together as one block however large it is. That is the
"cut at pressure minima" rule.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from fractions import Fraction
from typing import cast

from flab2bp.layout.hierarchy.partition import Unit
from flab2bp.spec import BuildSpec


def lane_capacity(spec: BuildSpec, item: str, *, external: bool) -> Fraction:
    """Items/second one lane of ``item`` carries, stack included."""
    stack = spec.planning_stack(item, external=external)
    return spec.lane_capacity * stack


def lanes_for(spec: BuildSpec, item: str, rate: Fraction, *, external: bool) -> int:
    if rate <= 0:
        return 0
    capacity = lane_capacity(spec, item, external=external)
    return max(1, math.ceil(rate / capacity))


def _crossing_lanes(spec: BuildSpec, item: str, rate: Fraction) -> int:
    """Lanes for an item crossing a cut, shared by every call site below.

    Always ``external=False``: a cut item is produced inside the parent spec
    and the crossing lane leaves on its producer's sorter, so it is judged at
    the PLACE stack whatever the bus also carries -- not the stack an item of
    the same name gets where it is *also* belted into the whole spec (a
    both-fed item such as universe-matrix's hydrogen, see
    ``BuildSpec.planning_stack``). Same reason ``_output_logical_lanes`` in
    ``strip_variants.py`` passes ``external=False`` for output lanes.
    """
    return lanes_for(spec, item, rate, external=False)


# --- depth levelling and the pressure profile --------------------------------


def recipe_depths(spec: BuildSpec) -> dict[str, int]:
    """Longest-path depth of each recipe from the external inputs."""
    producers: dict[str, list[str]] = defaultdict(list)
    for g in spec.groups:
        for item in g.outputs_per_machine:
            producers[item].append(g.recipe_id)
    by_recipe = {g.recipe_id: g for g in spec.groups}
    depth: dict[str, int] = {}

    def resolve(recipe: str, stack: frozenset[str]) -> int:
        if recipe in depth:
            return depth[recipe]
        if recipe in stack:
            return 0  # a coproduct cycle; break it rather than recurse forever
        best = 0
        for item in by_recipe[recipe].inputs_per_machine:
            for upstream in producers.get(item, ()):
                if upstream == recipe:
                    continue
                best = max(best, 1 + resolve(upstream, stack | {recipe}))
        depth[recipe] = best
        return best

    for recipe in by_recipe:
        resolve(recipe, frozenset())
    return depth


def depth_profile(
    spec: BuildSpec, *, depth: Mapping[str, int] | None = None
) -> list[dict[str, object]]:
    """Pressure of every boundary between depth <= d and depth > d."""
    if depth is None:
        depth = recipe_depths(spec)
    levels = sorted(set(depth.values()))
    rows: list[dict[str, object]] = []
    for d in levels[:-1]:
        made: dict[str, Fraction] = defaultdict(Fraction)
        taken_above: dict[str, Fraction] = defaultdict(Fraction)
        for g in spec.groups:
            if depth[g.recipe_id] <= d:
                for item, rate in g.outputs_per_machine.items():
                    made[item] += rate * g.count
            else:
                for item, rate in g.inputs_per_machine.items():
                    taken_above[item] += rate * g.count
        crossing = {
            item: min(made[item], taken_above[item]) for item in made if item in taken_above
        }
        rows.append(
            {
                "cut_after_depth": d,
                "recipes_below": sum(1 for r in depth.values() if r <= d),
                "machines_below": sum(g.count for g in spec.groups if depth[g.recipe_id] <= d),
                "item_pressure": len(crossing),
                "lane_pressure": sum(_crossing_lanes(spec, i, r) for i, r in crossing.items()),
                "items": sorted(crossing),
            }
        )
    return rows


def depth_pressure_blocks(
    spec: BuildSpec,
) -> tuple[list[list[Unit]], list[int], list[dict[str, object]]]:
    """Blocks formed by cutting at the LOCAL MINIMA of the depth pressure profile.

    A region between two chosen cuts stays together as one block however many
    machines it holds -- that is the whole point of the arm: high-pressure
    regions are solved integrated, and the knife goes where few belts cross.
    """
    depth = recipe_depths(spec)
    profile = depth_profile(spec, depth=depth)
    if not profile:
        cut_depths: list[int] = []
    else:
        lanes = [cast(int, row["lane_pressure"]) for row in profile]
        cut_depths = []
        for k, row in enumerate(profile):
            left = lanes[k - 1] if k else 1 << 30
            right = lanes[k + 1] if k + 1 < len(lanes) else 1 << 30
            if lanes[k] <= left and lanes[k] <= right:
                cut_depths.append(cast(int, row["cut_after_depth"]))

    uid = [5_000_000]
    blocks: list[list[Unit]] = []
    bands: list[tuple[int, int]] = []
    lo = min(depth.values(), default=0)
    hi = max(depth.values(), default=0)
    edges = [*sorted(cut_depths), hi]
    start = lo
    for edge in edges:
        if edge < start:
            continue
        bands.append((start, edge))
        start = edge + 1
    for band_lo, band_hi in bands:
        members = [g for g in spec.groups if band_lo <= depth[g.recipe_id] <= band_hi]
        if not members:
            continue
        block: list[Unit] = []
        for g in members:
            block.append(Unit(uid[0], g, g.count))
            uid[0] += 1
        blocks.append(block)
    return blocks, sorted(cut_depths), profile
