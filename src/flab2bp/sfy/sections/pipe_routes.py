"""Join rated fluid interfaces with clear rounded pipes or native junction turns."""

from __future__ import annotations

import heapq
import math
import time
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import count, product

from flab2bp.sfy.geometry import Vector, box_bounds, port_forward, quat_rotate
from flab2bp.sfy.labmap import LabMap, machine_class
from flab2bp.sfy.layout.model import Link, PipeAttachmentObj, PipeRun, Pose, SfyPlacement, pipe_ends
from flab2bp.sfy.layout.splines import (
    QUARTER_TURN_TANGENT,
    SplinePoint,
    concat,
    spline_length,
    straight,
)
from flab2bp.sfy.layout.validate import Context, WorldBox, pipe_chain, required_input_head_m
from flab2bp.sfy.registry import Buildable, Port, Registry
from flab2bp.sfy.sections.fluids import _JUNCTION, _NO_INDICATOR, _pipe_port
from flab2bp.sfy.sections.model import ProductionSection, SectionError, SectionPort, endpoint
from flab2bp.sfy.spec import SfyBuildSpec

type _Box = tuple[int, Vector, Vector]
type _Ref = tuple[int, str]
type _State = tuple[tuple[int, int, int], int]
_HEADINGS: tuple[Vector, ...] = (
    (1, 0, 0),
    (-1, 0, 0),
    (0, 1, 0),
    (0, -1, 0),
    (0, 0, 1),
    (0, 0, -1),
)
_EPS = 0.01


def _turn_radius(registry: Registry) -> float:
    """Quarter-circle radius with margin above the native curvature floor."""
    return registry.limits.pipe_min_bend_radius_cm * 1.1


def _pipe_envelope_radius(definition: Buildable) -> float:
    """Reserve the mesh's cross-section through arbitrary 3D curve tangents."""
    mesh = definition.mesh_bounds_cm
    if mesh is None:
        raise SectionError(f"{definition.class_name}: missing native pipe mesh", cause="data")
    return math.hypot(
        max(abs(bound[1]) for bound in mesh),
        max(abs(bound[2]) for bound in mesh),
    )


def _shift(point: Vector, heading: Vector, amount: float) -> Vector:
    return (
        point[0] + heading[0] * amount,
        point[1] + heading[1] * amount,
        point[2] + heading[2] * amount,
    )


def _bounds(owner: int, points: Sequence[Vector], radius: float = 0) -> _Box:
    return (
        owner,
        (
            min(p[0] for p in points) - radius,
            min(p[1] for p in points) - radius,
            min(p[2] for p in points) - radius,
        ),
        (
            max(p[0] for p in points) + radius,
            max(p[1] for p in points) + radius,
            max(p[2] for p in points) + radius,
        ),
    )


def _straight_box(a: Vector, b: Vector, radius: float) -> _Box:
    # Pipeline meshes have a transverse cross-section, not spherical end
    # caps. Inflating along the run would invent collisions between distinct
    # orthogonal connections of a perfectly ordinary native pipe junction.
    low = tuple(min(a[i], b[i]) - (radius if abs(a[i] - b[i]) < _EPS else 0) for i in range(3))
    high = tuple(max(a[i], b[i]) + (radius if abs(a[i] - b[i]) < _EPS else 0) for i in range(3))
    return (-1, (low[0], low[1], low[2]), (high[0], high[1], high[2]))


def _overlap(a: _Box, b: _Box) -> bool:
    return all(a[1][i] <= b[2][i] + _EPS and b[1][i] <= a[2][i] + _EPS for i in range(3))


def _holds(box: _Box, point: Vector) -> bool:
    return all(box[1][i] - _EPS <= point[i] <= box[2][i] + _EPS for i in range(3))


def _world_box(box: WorldBox) -> _Box:
    return _bounds(box.owner, box.corners())


def _heading(normal: Vector) -> int:
    ranked = sorted(range(6), key=lambda i: -sum(normal[j] * _HEADINGS[i][j] for j in range(3)))
    if sum(normal[j] * _HEADINGS[ranked[0]][j] for j in range(3)) < 0.999:
        raise SectionError(
            f"pipe endpoint normal {normal} is not an orthogonal side approach", cause="unsupported"
        )
    return ranked[0]


@dataclass(frozen=True, slots=True)
class _JunctionTurn:
    pose: Pose
    inlet: Port
    outlet: Port
    bounds: tuple[_Box, ...]

    def at(self, point: Vector) -> Pose:
        return Pose(*point, self.pose.yaw_deg, self.pose.pitch_deg, self.pose.roll_deg)

    def boxes(self, point: Vector) -> tuple[_Box, ...]:
        return tuple(
            (-1, _shift(low, point, 1), _shift(high, point, 1)) for _, low, high in self.bounds
        )


class _Routes:
    def __init__(
        self,
        spec: SfyBuildSpec,
        placement: SfyPlacement,
        registry: Registry,
        lab_map: LabMap,
        ids: Iterator[int],
        deadline: float,
    ) -> None:
        self.spec, self.placement, self.registry, self.lab_map = spec, placement, registry, lab_map
        self.ids, self.deadline = ids, deadline
        self.pipes: list[PipeRun] = []
        self.nodes: list[PipeAttachmentObj] = []
        self.links: list[Link] = []
        self.max_unpumped_z: float | None = None
        # The 2D hologram's preferred 199cm radius is not a legality floor.
        # Allow short gravity-fed descents, with margin above the native
        # 1.05*minimum radius for the cubic quarter-circle approximation.
        self.radius = _turn_radius(registry)
        self.definitions: list[tuple[Fraction, Buildable]] = []
        for tier in spec.pipe_tiers:
            base = machine_class(lab_map, tier.item_id)
            cls = _NO_INDICATOR.get(base)
            if cls is None or cls not in registry.buildables:
                raise SectionError(f"unsupported pipeline family {base}", cause="unsupported")
            definition = registry.buildables[cls]
            native = definition.pipe_flow_limit_m3s
            if (
                native is None
                or definition.mesh_bounds_cm is None
                or definition.mesh_length_cm is None
            ):
                raise SectionError(
                    f"{cls}: missing native pipe capacity or mesh", cause="unsupported"
                )
            self.definitions.append(
                (min(tier.cubic_metres_per_second, Fraction(str(native))), definition)
            )
        if not self.definitions:
            raise SectionError("fluid interfaces require an available pipe tier", cause="capacity")
        self.pipe_radius = max(
            max(abs(v) for bound in definition.mesh_bounds_cm for v in bound[1:])
            for _, definition in self.definitions
            if definition.mesh_bounds_cm is not None
        )
        self.margin = self.pipe_radius + 1
        junction = registry.buildables[_JUNCTION]
        self.junction_radius = max(
            math.dist((0, 0, 0), port.translation) for port in junction.ports
        )
        self.junction_turns: dict[tuple[int, int], _JunctionTurn] = {}
        for pose in (Pose(0, 0, 0, 0), Pose(0, 0, 0, 0, 0, 90), Pose(0, 0, 0, 90, 0, 90)):
            ports = {
                _heading(port_forward(pose.transform(), port)): port
                for port in junction.ports
                if port.kind == "pipe"
            }
            boxes = tuple((-1, *box_bounds(box, pose.transform())) for box in junction.clearance)
            for incoming, outgoing in product(ports, repeat=2):
                if incoming // 2 != outgoing // 2:
                    self.junction_turns[(incoming ^ 1, outgoing)] = _JunctionTurn(
                        pose, ports[incoming], ports[outgoing], boxes
                    )
        # Axis-aligned straight runs need only the transverse mesh extent;
        # a sampled bend rotates its square section and needs its diagonal
        # kept inside the designer walls.
        self.wall_margin = (
            max(_pipe_envelope_radius(definition) for _, definition in self.definitions) + 1
        )
        self.half = placement.designer.half_cm
        self.height = placement.designer.height_cm
        context = Context(placement, spec, registry, {})
        self.obstacles = [_world_box(box) for box in context.boxes]
        self.obstacles.extend(
            _world_box(box) for boxes in context.conveyor_boxes.values() for box in boxes
        )
        self.obstacles.extend(
            _world_box(box) for boxes in context.pipe_chains.values() for box in boxes
        )
        grid = registry.limits.hologram_grid_cm
        if grid is None:
            raise SectionError(
                "pipe routing requires a registry placement grid", cause="unsupported"
            )
        self.step = math.ceil(4 * self.radius / grid) * grid
        self.grid = (
            tuple(
                -self.half + self.wall_margin + i * self.step
                for i in range(math.floor((2 * self.half - 2 * self.wall_margin) / self.step) + 1)
            ),
            tuple(
                -self.half + self.wall_margin + i * self.step
                for i in range(math.floor((2 * self.half - 2 * self.wall_margin) / self.step) + 1)
            ),
            tuple(
                self.wall_margin + i * self.step
                for i in range(math.floor((self.height - 2 * self.wall_margin) / self.step) + 1)
            ),
        )

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise SectionError(
                "fluid routing exceeded the original layout deadline", cause="deadline"
            )

    def inside(self, box: _Box) -> bool:
        return (
            box[1][0] >= -self.half
            and box[2][0] <= self.half
            and box[1][1] >= -self.half
            and box[2][1] <= self.half
            and box[1][2] >= 0
            and box[2][2] <= self.height
        )

    def clear(self, box: _Box, obstacles: Sequence[_Box]) -> bool:
        return self.inside(box) and not any(_overlap(box, other) for other in obstacles)

    def at(self, ref: _Ref) -> tuple[Vector, Vector]:
        for node in self.nodes:
            if node.id == ref[0]:
                local = replace(self.placement, pipe_attachments=(node,))
                return endpoint(local, *ref, self.registry)
        return endpoint(self.placement, *ref, self.registry)

    def search(self, source: _Ref, target: _Ref, *, junctions: bool = False) -> tuple[Vector, ...]:
        self.check()
        start, facing = self.at(source)
        finish, normal = self.at(target)
        if (
            self.max_unpumped_z is not None
            and max(start[2], finish[2]) > self.max_unpumped_z + _EPS
        ):
            raise SectionError(
                f"fluid route needs an inlet above its {self.max_unpumped_z:g}cm producer; "
                "no factory output head is established",
                cause="hydraulic",
            )
        first_heading, last_heading = _heading(facing), _heading(normal) ^ 1
        # Exempt only the boxes owning an actual connected endpoint. In
        # particular this does not exempt the rest of a connected spline or
        # another machine sharing its section's bounding rectangle.
        obstacles = [
            box
            for box in self.obstacles
            if not (
                box[0] == source[0]
                and _holds(box, start)
                or box[0] == target[0]
                and _holds(box, finish)
            )
        ]
        delta = (finish[0] - start[0], finish[1] - start[1], finish[2] - start[2])
        span = math.dist(start, finish)
        if (
            span > _EPS
            and first_heading == last_heading
            and sum(delta[i] * _HEADINGS[first_heading][i] for i in range(3)) >= span - _EPS
            and self.clear(_straight_box(start, finish, self.margin), obstacles)
        ):
            return start, finish
        radius = self.junction_radius if junctions else self.radius
        minimum_pipe = min(
            definition.mesh_length_cm / 2
            for _, definition in self.definitions
            if definition.mesh_length_cm is not None
        )
        axes = tuple(
            tuple(
                sorted(
                    set(
                        (
                            *self.grid[i],
                            start[i],
                            finish[i],
                            *((((start[i] + finish[i]) / 2),) if junctions else ()),
                            *[
                                start[i] + sign * 2 * radius
                                for sign in (-1, 1)
                                if (self.wall_margin if i == 2 else -self.half + self.wall_margin)
                                <= start[i] + sign * 2 * radius
                                <= (self.height if i == 2 else self.half) - self.wall_margin
                            ],
                            *[
                                finish[i] + sign * 2 * radius
                                for sign in (-1, 1)
                                if (self.wall_margin if i == 2 else -self.half + self.wall_margin)
                                <= finish[i] + sign * 2 * radius
                                <= (self.height if i == 2 else self.half) - self.wall_margin
                            ],
                        )
                    )
                )
            )
            for i in range(3)
        )
        if self.max_unpumped_z is not None:
            axes = (axes[0], axes[1], tuple(z for z in axes[2] if z <= self.max_unpumped_z + _EPS))
        begin = (axes[0].index(start[0]), axes[1].index(start[1]), axes[2].index(start[2]))
        end = (axes[0].index(finish[0]), axes[1].index(finish[1]), axes[2].index(finish[2]))
        initial: _State = (begin, -1)
        queue: list[tuple[float, int, float, _State]] = [(math.dist(start, finish), 0, 0, initial)]
        serial = count(1)
        costs: dict[_State, float] = {initial: 0.0}
        parents: dict[_State, _State] = {}
        cache: dict[tuple[Vector, Vector], bool] = {}

        def position(node: tuple[int, int, int]) -> Vector:
            return (axes[0][node[0]], axes[1][node[1]], axes[2][node[2]])

        while queue:
            self.check()
            _, _, cost, state = heapq.heappop(queue)
            if cost != costs[state]:
                continue
            node, arrival = state
            here = position(node)
            if node == end and arrival == last_heading:
                path = [here]
                while state in parents:
                    state = parents[state]
                    path.append(position(state[0]))
                return tuple(reversed(path))
            choices = (
                (first_heading,)
                if arrival < 0
                else tuple(d for d in range(6) if d // 2 != arrival // 2)
            )
            for direction in choices:
                axis, sign = direction // 2, 1 if direction % 2 == 0 else -1
                if arrival >= 0:
                    if junctions:
                        turn = self.junction_turns[(arrival, direction)]
                        if not all(self.clear(box, self.obstacles) for box in turn.boxes(here)):
                            continue
                        previous_state = parents.get(state)
                        if previous_state is not None and previous_state[1] >= 0:
                            previous_node, previous_arrival = previous_state
                            previous_turn = self.junction_turns[(previous_arrival, arrival)]
                            if any(
                                _overlap(a, b)
                                for a in turn.boxes(here)
                                for b in previous_turn.boxes(position(previous_node))
                            ):
                                continue
                    else:
                        arc_start = _shift(here, _HEADINGS[arrival], -radius)
                        arc_end = _shift(here, _HEADINGS[direction], radius)
                        if not self.clear(
                            _bounds(-1, (arc_start, here, arc_end), self.margin), obstacles
                        ):
                            continue
                indices = range(node[axis] + sign, len(axes[axis]) if sign > 0 else -1, sign)
                for index in indices:
                    candidate_list = list(node)
                    candidate_list[axis] = index
                    candidate = (candidate_list[0], candidate_list[1], candidate_list[2])
                    there = position(candidate)
                    distance = abs(there[axis] - here[axis])
                    final = candidate == end and direction == last_heading
                    minimum = radius if arrival < 0 or final else 2 * radius
                    if distance < minimum - _EPS:
                        continue
                    if junctions and _EPS < distance - minimum <= minimum_pipe:
                        continue
                    key = (here, there)
                    free = cache.get(key)
                    if free is None:
                        run_start = (
                            here
                            if not junctions or arrival < 0
                            else _shift(here, _HEADINGS[direction], radius)
                        )
                        run_end = (
                            there
                            if not junctions or final
                            else _shift(there, _HEADINGS[direction], -radius)
                        )
                        free = self.clear(_straight_box(run_start, run_end, self.margin), obstacles)
                        cache[key] = free
                    if not free:
                        if junctions:
                            continue
                        break
                    next_state: _State = (candidate, direction)
                    new_cost = cost + distance
                    if new_cost >= costs.get(next_state, math.inf):
                        continue
                    costs[next_state] = new_cost
                    parents[next_state] = state
                    estimate = sum(abs(there[i] - finish[i]) for i in range(3))
                    heapq.heappush(queue, (new_cost + estimate, next(serial), new_cost, next_state))
        shape = "junction-turn" if junctions else "native-radius pipe"
        raise SectionError(f"no clear {shape} route from {source} to {target}", cause="routing")

    def pieces(self, path: Sequence[Vector]) -> list[tuple[SplinePoint, ...]]:
        pieces: list[tuple[SplinePoint, ...]] = []
        max_piece = self.registry.limits.pipe_max_spline_cm / 4
        pull = self.radius * QUARTER_TURN_TANGENT
        for index, (a, b) in enumerate(zip(path, path[1:], strict=False)):
            distance = math.dist(a, b)
            heading = ((b[0] - a[0]) / distance, (b[1] - a[1]) / distance, (b[2] - a[2]) / distance)
            start = _shift(a, heading, self.radius if index else 0)
            finish = _shift(b, heading, -self.radius if index < len(path) - 2 else 0)
            length = math.dist(start, finish)
            if length > _EPS:
                chunks = math.ceil(length / max_piece)
                for chunk in range(chunks):
                    piece = straight(
                        _shift(start, heading, chunk * length / chunks),
                        heading,
                        length / chunks,
                    )
                    if length / chunks < 2 * math.dist((0, 0, 0), piece[0][2]):
                        # The standalone straight builder's minimum tangent
                        # overshoots short internal leads between two bends.
                        tangent = _shift((0, 0, 0), heading, length / chunks / 2)
                        piece = (
                            (piece[0][0], piece[0][1], tangent),
                            (piece[-1][0], tangent, piece[-1][2]),
                        )
                    pieces.append(piece)
            if index < len(path) - 2:
                c = path[index + 2]
                following = (
                    (c[0] - b[0]) / math.dist(b, c),
                    (c[1] - b[1]) / math.dist(b, c),
                    (c[2] - b[2]) / math.dist(b, c),
                )
                end = _shift(b, following, self.radius)
                pieces.append(
                    (
                        (finish, heading, _shift((0, 0, 0), heading, pull)),
                        (end, _shift((0, 0, 0), following, pull), following),
                    )
                )
        return pieces

    def connect(
        self,
        source: _Ref,
        target: _Ref,
        item: str,
        rate: Fraction,
        path: Sequence[Vector] | None = None,
    ) -> None:
        if not any(rate <= capacity for capacity, _ in self.definitions):
            raise SectionError(
                f"{item}: pipe branch needs {rate} m³/s above available capacity", cause="capacity"
            )
        node_count, pipe_count, link_count, obstacle_count = (
            len(self.nodes),
            len(self.pipes),
            len(self.links),
            len(self.obstacles),
        )

        def restore() -> None:
            del self.nodes[node_count:]
            del self.pipes[pipe_count:]
            del self.links[link_count:]
            del self.obstacles[obstacle_count:]

        try:
            start, facing = self.at(source)
            finish, normal = self.at(target)
            if path is not None or math.dist(start, finish) <= _EPS:
                self._connect_curves(source, target, item, rate, path)
                return
            curved: Sequence[Vector] | None = None
            # Straight rises need no fitting. Bent height changes and
            # reversals instead prefer the game's native four-way junctions.
            prefer_junctions = (
                abs(start[2] - finish[2]) > _EPS and math.dist(start[:2], finish[:2]) > _EPS
            ) or _heading(facing) == _heading(normal)
            if not prefer_junctions:
                try:
                    curved = self.search(source, target)
                except SectionError as exc:
                    if exc.cause != "routing":
                        raise
                    prefer_junctions = True
                else:
                    headings = [
                        _heading((b[0] - a[0], b[1] - a[1], b[2] - a[2]))
                        for a, b in zip(curved, curved[1:], strict=False)
                    ]
                    prefer_junctions = len(headings) > 1 and (
                        any(heading // 2 == 2 for heading in headings)
                        or any(
                            (heading ^ 1) in headings[:index]
                            for index, heading in enumerate(headings)
                        )
                    )
            if prefer_junctions:
                try:
                    self._connect_junctions(
                        source, target, item, rate, self.search(source, target, junctions=True)
                    )
                    return
                except SectionError as exc:
                    restore()
                    if exc.cause != "routing":
                        raise
                    # A short descent may fit legal rounded pipe but not
                    # two junctions with their native connector offsets.
            if curved is None:
                curved = self.search(source, target)
            self._connect_curves(source, target, item, rate, curved)
        except Exception:
            restore()
            raise

    def _connect_junctions(
        self,
        source: _Ref,
        target: _Ref,
        item: str,
        rate: Fraction,
        path: Sequence[Vector],
    ) -> None:
        definition = next(
            (definition for capacity, definition in self.definitions if rate <= capacity), None
        )
        if definition is None:
            raise SectionError(
                f"{item}: junction route exceeds available pipe capacity", cause="capacity"
            )
        if definition.mesh_length_cm is None:
            raise SectionError("junction route requires native pipe mesh length", cause="data")
        headings = [
            _heading((b[0] - a[0], b[1] - a[1], b[2] - a[2]))
            for a, b in zip(path, path[1:], strict=False)
        ]
        pairs: list[tuple[_Ref, _Ref]] = []
        previous = source
        for index, point in enumerate(path[1:-1]):
            self.check()
            turn = self.junction_turns[(headings[index], headings[index + 1])]
            boxes = turn.boxes(point)
            if not all(self.clear(box, self.obstacles) for box in boxes):
                raise SectionError(
                    "junction turn has no clear native body envelope", cause="routing"
                )
            pose = turn.at(point)
            if self.max_unpumped_z is not None:
                for port in (turn.inlet, turn.outlet):
                    offset = quat_rotate(pose.transform().rotation, port.translation)
                    if point[2] + offset[2] > self.max_unpumped_z + _EPS:
                        raise SectionError(
                            "junction turn exceeds available producer head", cause="routing"
                        )
            node = PipeAttachmentObj(next(self.ids), _JUNCTION, pose)
            self.nodes.append(node)
            self.obstacles.extend((node.id, low, high) for _, low, high in boxes)
            pairs.append((previous, (node.id, turn.inlet.name)))
            previous = (node.id, turn.outlet.name)
        pairs.append((previous, target))
        for begin, end in pairs:
            start, normal_a = self.at(begin)
            finish, normal_b = self.at(end)
            distance = math.dist(start, finish)
            if distance <= _EPS:
                if sum(normal_a[i] * normal_b[i] for i in range(3)) > -0.999:
                    raise SectionError(
                        "touching junction ports do not oppose each other", cause="routing"
                    )
            elif distance <= definition.mesh_length_cm / 2:
                raise SectionError(
                    "junction turns leave less than a native pipe segment", cause="routing"
                )
            obstacles = [
                box
                for box in self.obstacles
                if not (
                    box[0] == begin[0]
                    and _holds(box, start)
                    or box[0] == end[0]
                    and _holds(box, finish)
                )
            ]
            if not self.clear(_straight_box(start, finish, self.margin), obstacles):
                raise SectionError(
                    "junction connection has no clear straight pipe envelope", cause="routing"
                )
            self._connect_curves(begin, end, item, rate, (start, finish))

    def _connect_curves(
        self,
        source: _Ref,
        target: _Ref,
        item: str,
        rate: Fraction,
        path: Sequence[Vector] | None = None,
    ) -> None:
        start, normal_a = self.at(source)
        end, normal_b = self.at(target)
        if self.max_unpumped_z is not None and max(start[2], end[2]) > self.max_unpumped_z + _EPS:
            raise SectionError(
                f"{item}: unpumped outlet would require an uphill fluid connection",
                cause="hydraulic",
            )
        if (
            math.dist(start, end) <= _EPS
            and sum(normal_a[i] * normal_b[i] for i in range(3)) < -0.999
        ):
            self.links.append(Link(source, target))
            return
        definition = next(
            (definition for capacity, definition in self.definitions if rate <= capacity), None
        )
        if definition is None:
            raise SectionError(
                f"{item}: pipe branch needs {rate} m³/s above available capacity", cause="capacity"
            )
        if definition.mesh_length_cm is None:
            raise SectionError(
                f"{definition.class_name}: missing pipe mesh length", cause="unsupported"
            )
        selected = path if path is not None else self.search(source, target)
        pieces = self.pieces(selected)
        chunks: list[tuple[SplinePoint, ...]] = []
        pending: list[tuple[SplinePoint, ...]] = []
        length = 0.0
        for piece in pieces:
            piece_length = spline_length(piece)
            chord = math.dist(piece[0][0], piece[-1][0])
            # Put actor seams inside flat runs, with mesh-width leads on
            # both sides. At a bend boundary the two sampled mesh envelopes
            # overlap even though their centreline tangents agree.
            if (
                length + piece_length >= self.registry.limits.pipe_max_spline_cm / 2
                and abs(piece_length - chord) <= _EPS
                and chord > 2 * self.margin
            ):
                heading = tuple((piece[-1][0][i] - piece[0][0][i]) / chord for i in range(3))
                direction = (heading[0], heading[1], heading[2])
                half = chord / 2
                before = straight(piece[0][0], direction, half)
                after = straight(_shift(piece[0][0], direction, half), direction, half)
                chunks.append(concat(*pending, before))
                pending, length = [after], half
            else:
                pending.append(piece)
                length += piece_length
        if pending:
            chunk = concat(*pending)
            if (
                chunks
                and sum(math.dist(a[0], b[0]) for a, b in zip(chunk, chunk[1:], strict=False))
                <= definition.mesh_length_cm / 2
            ):
                chunks[-1] = concat(chunks[-1], chunk)
            else:
                chunks.append(chunk)
        entry, exit_ = pipe_ends(self.registry, definition.class_name)
        previous = source
        for points in chunks:
            self.check()
            if (
                spline_length(points) > self.registry.limits.pipe_max_spline_cm
                or sum(math.dist(a[0], b[0]) for a, b in zip(points, points[1:], strict=False))
                <= definition.mesh_length_cm / 2
            ):
                raise SectionError(
                    f"{item}: routed pipe violates native spline length", cause="geometry"
                )
            run = PipeRun(next(self.ids), definition.class_name, points, item, rate)
            self.pipes.append(run)
            self.links.append(Link(previous, (run.id, entry)))
            previous = (run.id, exit_)
            self.obstacles.extend(_world_box(box) for box in pipe_chain(run, self.registry))
        self.links.append(Link(previous, target))

    def branch_node(self, terminal: _Ref, item: str, rate: Fraction) -> PipeAttachmentObj:
        definition = self.registry.buildables[_JUNCTION]
        branch = _pipe_port(definition, (0, 1, 0))
        at, _ = self.at(terminal)
        candidates = sorted(
            product(*self.grid), key=lambda p: sum(abs(p[i] - at[i]) for i in range(3))
        )
        for point in candidates:
            self.check()
            if self.max_unpumped_z is not None and point[2] > self.max_unpumped_z + _EPS:
                continue
            node = PipeAttachmentObj(next(self.ids), _JUNCTION, Pose(*point, 0))
            boxes = [
                (node.id, *box_bounds(box, node.pose.transform())) for box in definition.clearance
            ]
            if not all(self.clear(box, self.obstacles) for box in boxes):
                continue
            self.nodes.append(node)
            self.obstacles.extend(boxes)
            try:
                self.connect(terminal, (node.id, branch.name), item, rate)
            except SectionError as exc:
                self.nodes.pop()
                del self.obstacles[-len(boxes) :]
                if exc.cause != "routing":
                    raise
                continue
            return node
        raise SectionError(
            f"{item}: no obstacle-free pipe junction and side approach", cause="bounds"
        )


def connect_fluid_ports(
    spec: SfyBuildSpec,
    placement: SfyPlacement,
    sections: Sequence[ProductionSection],
    boundaries: Sequence[SectionPort],
    registry: Registry,
    lab_map: LabMap,
    *,
    ids: Iterator[int],
    deadline: float,
) -> SfyPlacement:
    """Connect each balanced fluid network without inventing directional pipe links."""
    groups: defaultdict[str, list[SectionPort]] = defaultdict(list)
    for section in sections:
        for port in (*section.inputs, *section.outputs):
            if port.kind == "pipe":
                groups[port.item_id].append(port)
    for port in boundaries:
        if port.kind == "pipe":
            groups[port.item_id].append(port)
    if not groups:
        return placement
    routes = _Routes(spec, placement, registry, lab_map, ids, deadline)
    definition = registry.buildables[_JUNCTION]
    left, right = _pipe_port(definition, (-1, 0, 0)), _pipe_port(definition, (1, 0, 0))
    for item, ports in sorted(groups.items()):
        producer_heights = [
            endpoint(placement, port.object_id, port.port, registry)[0][2]
            for section in sections
            for port in section.outputs
            if port.kind == "pipe" and port.item_id == item
        ]
        # These joins are upstream of the boundary pump. A factory output's
        # unknown head cannot justify an upward loop, even if both endpoints
        # ultimately sit lower; every route centreline stays below this cap.
        routes.max_unpumped_z = min(producer_heights) if producer_heights else None
        sources = sum((p.items_per_second for p in ports if p.direction == "output"), Fraction())
        sinks = sum((p.items_per_second for p in ports if p.direction == "input"), Fraction())
        if not sources or not sinks or sources != sinks:
            raise SectionError(
                f"{item}: fluid section rates do not balance ({sources} in, {sinks} out)",
                cause="balance",
            )
        if len({(p.object_id, p.port) for p in ports}) != len(ports):
            raise SectionError(f"{item}: duplicate pipe interface", cause="balance")
        if len(ports) == 2:
            routes.connect(
                (ports[0].object_id, ports[0].port),
                (ports[1].object_id, ports[1].port),
                item,
                sources,
            )
            continue
        nodes = [routes.branch_node((p.object_id, p.port), item, p.items_per_second) for p in ports]
        running = Fraction()
        for index, (port, node) in enumerate(zip(ports, nodes, strict=True)):
            running += port.items_per_second * (1 if port.direction == "output" else -1)
            if index + 1 < len(nodes):
                routes.connect(
                    (node.id, right.name), (nodes[index + 1].id, left.name), item, abs(running)
                )
    result = replace(
        placement,
        pipes=(*placement.pipes, *routes.pipes),
        pipe_attachments=(*placement.pipe_attachments, *routes.nodes),
        links=(*placement.links, *routes.links),
    )
    # Stack construction cannot know the apex of a subsequently routed local
    # feed. Declare the complete pre-pump head requirement using exactly the
    # same native connection/centreline calculation that validates it.
    lanes = []
    for lane in result.stack_lanes:
        if lane.kind == "pipe" and lane.input_per_second > 0:
            head = required_input_head_m(result, lane.bottom, registry)
            if not math.isfinite(head):
                raise SectionError(
                    f"{lane.item_id}: routed input has no source-backed head requirement",
                    cause="hydraulic",
                )
            lane = replace(lane, required_input_head_m=head)
        lanes.append(lane)
    return replace(result, stack_lanes=tuple(lanes))
