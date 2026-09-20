"""Readable paired labels on the front painted-beam frame."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from dataclasses import replace
from fractions import Fraction
from itertools import chain

from flab2bp.lab.data import load_vendored
from flab2bp.lab.url import Game
from flab2bp.sfy.geometry import Vector, box_bounds
from flab2bp.sfy.layout.model import BeamObj, Pose, SfyPlacement, SignObj
from flab2bp.sfy.layout.validate import (
    TOUCH_CM,
    Context,
    WorldBox,
    _apart,
    _penetration,
    _place_box,
    attachment_boxes,
    beam_box,
)
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.sections.model import SectionError, endpoint

_SIGN = "Build_StandaloneWidgetSign_SmallWide_C"
_BEAM = "Build_Beam_Painted_C"


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise SectionError("connection sign placement exceeded its deadline", cause="deadline")


def _rate(value: Fraction) -> str:
    """Exact per-minute text: short terminating decimals, otherwise a fraction."""
    value *= 60
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    places = max(twos, fives)
    if denominator != 1 or places > 6:
        return str(value)
    if not places:
        return str(value.numerator)
    scaled = value.numerator * (10**places // value.denominator)
    whole, decimal = divmod(scaled, 10**places)
    return f"{whole}.{decimal:0{places}d}".rstrip("0")


def _letter(index: int) -> str:
    """Deterministic lane labels, including factories with more than 26 lanes."""
    result = ""
    while index >= 0:
        index, digit = divmod(index, 26)
        result = chr(ord("A") + digit) + result
        index -= 1
    return result


def _clear(boxes: tuple[WorldBox, ...], obstacles: list[WorldBox]) -> bool:
    return all(
        _apart(box, obstacle) or _penetration(box, obstacle) <= TOUCH_CM
        for box in boxes
        for obstacle in obstacles
    )


def _inside(box: WorldBox, placement: SfyPlacement) -> bool:
    half = placement.designer.half_cm
    return (
        all(abs(box.centre[i]) + box.reach[i] <= half + TOUCH_CM for i in (0, 1))
        and box.centre[2] - box.reach[2] >= -TOUCH_CM
        and box.centre[2] + box.reach[2] <= placement.designer.height_cm + TOUCH_CM
    )


def _mount_pair(
    placement: SfyPlacement,
    registry: Registry,
    point: Vector,
    *,
    upper: bool,
    header: str,
    text: str,
    label: str,
    obstacles: list[WorldBox],
    ids: Iterator[int],
    deadline: float,
) -> tuple[tuple[SignObj, SignObj], BeamObj, tuple[WorldBox, ...]]:
    """Mount on a front rail, with an unobstructed view from blueprint +X.

    The reference's front sign faces local +Y at yaw -90. Labels belong on
    the frame, not at the nearest free point behind a transport connection.
    Lower pairs stand on the base rail; upper pairs hang from the roof rail.
    """
    definition = registry.buildables[_SIGN]
    local_bounds = [box_bounds(box, Pose(0, 0, 0, 0).transform()) for box in definition.clearance]
    half_height = max(max(abs(low[2]), abs(high[2])) for low, high in local_bounds)
    half_depth = max(max(abs(low[1]), abs(high[1])) for low, high in local_bounds)
    beam_size = registry.buildables[_BEAM].beam_size_cm
    grid = registry.limits.hologram_grid_cm
    assert beam_size is not None and grid is not None
    pair_height = 4 * half_height
    rails = []
    for beam in placement.beams:
        if beam.class_name != _BEAM or abs(beam.pose.z - point[2]) >= 1.0:
            continue
        box = beam_box(beam, registry)
        if abs(box.axes[0][1]) > 0.999:
            rails.append(box)
    if not rails:
        raise SectionError(f"{label} {header}: no front frame rail for signs", cause="bounds")
    front = max(rail.centre[0] for rail in rails)
    rails = [rail for rail in rails if abs(rail.centre[0] - front) < 1.0]
    candidates: list[tuple[float, float, float, float, float]] = []
    for rail in rails:
        _check(deadline)
        # Keep the sign outside its support without leaving the designer.
        face_x = min(rail.centre[0] + rail.reach[0] + 2 * half_depth, placement.designer.half_cm)
        sign_x = face_x - half_depth
        support_x = face_x - 2 * half_depth - beam_size / 2
        surface = rail.centre[2] + (-rail.reach[2] if upper else rail.reach[2])
        low = math.ceil((rail.centre[1] - rail.reach[1] + beam_size / 2) / grid)
        high = math.floor((rail.centre[1] + rail.reach[1] - beam_size / 2) / grid)
        for step in range(low, high + 1):
            y = step * grid
            candidates.append((abs(y - point[1]), y, sign_x, support_x, surface))
    for _, y, sign_x, support_x, surface in sorted(candidates):
        _check(deadline)
        support = BeamObj(
            -1, _BEAM, Pose(support_x, y, surface, -90, -90 if upper else 90), pair_height
        )
        header_z = surface - half_height if upper else surface + 3 * half_height
        pair = (
            SignObj(-2, _SIGN, Pose(sign_x, y, header_z, -90), header, label, "BPW_Sign4x1_5"),
            SignObj(
                -3,
                _SIGN,
                Pose(sign_x, y, header_z - 2 * half_height, -90),
                text,
                label,
                "BPW_Sign4x1_3",
            ),
        )
        sign_boxes = tuple(
            _place_box(box, sign.pose.transform(), sign.id, "connection sign")
            for sign in pair
            for box in definition.clearance
        )
        boxes = (beam_box(support, registry), *sign_boxes)
        if not all(_inside(box, placement) for box in boxes) or not _clear(boxes, obstacles):
            continue
        if any(
            not _apart(box, other) and _penetration(box, other) > TOUCH_CM
            for i, box in enumerate(boxes)
            for other in boxes[i + 1 :]
        ):
            continue
        # Body clearance alone permits a lift directly in front of a sign.
        # Reserve the entire text rectangle's straight sightline to the front.
        sightlines = []
        for box in sign_boxes:
            face = box.centre[0] + box.reach[0]
            distance = placement.designer.half_cm - face
            if distance > TOUCH_CM:
                sightlines.append(
                    WorldBox(
                        box.owner,
                        "sign visibility",
                        (face + distance / 2, box.centre[1], box.centre[2]),
                        ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                        (distance / 2, box.reach[1], box.reach[2]),
                    )
                )
        if not _clear(tuple(sightlines), obstacles):
            continue
        support = replace(support, id=next(ids))
        pair = (replace(pair[0], id=next(ids)), replace(pair[1], id=next(ids)))
        return pair, support, boxes
    raise SectionError(
        f"{label} {header}: no unobstructed sign pair fits on the front frame", cause="bounds"
    )


def add_connection_signs(
    placement: SfyPlacement,
    registry: Registry,
    *,
    ids: Iterator[int],
    deadline: float,
) -> SfyPlacement:
    """Label every lane on the front frame with local rates, never trunk load."""
    _check(deadline)
    if not placement.stack_lanes:
        return placement
    sign = registry.buildables.get(_SIGN)
    beam = registry.buildables.get(_BEAM)
    if (
        sign is None
        or not sign.clearance
        or beam is None
        or beam.beam_size_cm is None
        or registry.limits.hologram_grid_cm is None
    ):
        raise SectionError(
            "connection signs lack native sign, beam or grid dimensions", cause="data"
        )
    dataset = load_vendored(Game.SFY)
    context = Context(placement, None, registry, {})
    # Sign bodies are soft in the game registry. We deliberately reserve their
    # actual complete body against ALL geometry, not merely hard machinery boxes.
    obstacles = list(
        chain(
            context.boxes,
            chain.from_iterable(attachment_boxes(obj, registry) for obj in placement.attachments),
            chain.from_iterable(context.conveyor_boxes.values()),
            chain.from_iterable(context.pipe_chains.values()),
        )
    )
    signs = list(placement.signs)
    beams = list(placement.beams)
    for index, lane in enumerate(sorted(placement.stack_lanes, key=lambda lane: lane.item_id)):
        _check(deadline)
        try:
            name = dataset.item(lane.item_id).name
        except KeyError as exc:
            raise SectionError(
                f"{lane.item_id}: no native sign display name", cause="data"
            ) from exc
        for upper, ref, header, rate in (
            (not lane.upward, lane.flow_start, "INPUT", lane.input_per_second),
            (lane.upward, lane.flow_end, "OUTPUT", lane.output_per_second),
        ):
            point, _ = endpoint(placement, *ref, registry)
            unit = " m³/min" if lane.kind == "pipe" else "/min"
            text = f"{name} {_rate(rate)}{unit}" if rate else f"{name} PASS THROUGH"
            pair, support, boxes = _mount_pair(
                placement,
                registry,
                point,
                upper=upper,
                header=header,
                text=text,
                label=_letter(index),
                obstacles=obstacles,
                ids=ids,
                deadline=deadline,
            )
            signs.extend(pair)
            beams.append(support)
            obstacles.extend(boxes)
    return replace(placement, signs=tuple(signs), beams=tuple(beams))
