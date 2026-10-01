"""Join rated fluid interfaces with clear rounded pipes or native junction turns."""

from __future__ import annotations

import heapq
import math
import time
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import count, permutations, product

from flab2bp.sfy.geometry import Vector, box_bounds, port_forward, quat_rotate, world_port
from flab2bp.sfy.labmap import LabMap, machine_class
from flab2bp.sfy.layout.model import Link, PipeAttachmentObj, PipeRun, Pose, SfyPlacement, pipe_ends
from flab2bp.sfy.layout.splines import (
    QUARTER_TURN_TANGENT,
    SplinePoint,
    concat,
    spline_length,
    straight,
)
from flab2bp.sfy.layout.validate import (
    Context,
    WorldBox,
    attachment_boxes,
    pipe_chain,
    required_input_head_m,
)
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
        self.curved_turns: dict[tuple[int, int], tuple[_Box, ...]] = {}
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
            _world_box(box)
            for obj in placement.attachments
            for box in attachment_boxes(obj, registry)
        )
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

    def clear_bend(
        self, here: Vector, incoming: int, outgoing: int, obstacles: Sequence[_Box]
    ) -> bool:
        before, after = _HEADINGS[incoming], _HEADINGS[outgoing]
        start = _shift(here, before, -self.radius)
        finish = _shift(here, after, self.radius)
        broad = _bounds(-1, (start, here, finish), self.margin)
        contacts = [other for other in obstacles if _overlap(broad, other)]
        if not contacts and self.inside(broad):
            return True
        key = (incoming, outgoing)
        boxes = self.curved_turns.get(key)
        if boxes is None:
            # The virtual elbow's inflated cuboid has fictitious end caps.
            # Beside a pump or frame post, test the same oriented native mesh
            # envelope as validation instead of rejecting that empty space.
            pull = self.radius * QUARTER_TURN_TANGENT
            points = (
                (_shift((0, 0, 0), before, -self.radius), before, _shift((0, 0, 0), before, pull)),
                (_shift((0, 0, 0), after, self.radius), _shift((0, 0, 0), after, pull), after),
            )
            boxes = tuple(
                _bounds(-1, box.corners(), 1)
                for _, definition in self.definitions
                for box in pipe_chain(
                    PipeRun(-1, definition.class_name, points, "", Fraction()), self.registry
                )
            )
            self.curved_turns[key] = boxes
        return all(
            self.clear(
                (
                    -1,
                    (low[0] + here[0], low[1] + here[1], low[2] + here[2]),
                    (high[0] + here[0], high[1] + here[1], high[2] + here[2]),
                ),
                contacts,
            )
            for _, low, high in boxes
        )

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
        if not junctions and first_heading // 2 != last_heading // 2:
            first_axis, last_axis = first_heading // 2, last_heading // 2
            first_run = delta[first_axis] * _HEADINGS[first_heading][first_axis]
            last_run = delta[last_axis] * _HEADINGS[last_heading][last_axis]
            if first_run >= self.radius and last_run >= self.radius:
                middle_axis = 3 - first_axis - last_axis
                corner = _shift(start, _HEADINGS[first_heading], first_run)
                if abs(delta[middle_axis]) <= _EPS:
                    if (
                        self.clear(_straight_box(start, corner, self.margin), obstacles)
                        and self.clear(_straight_box(corner, finish, self.margin), obstacles)
                        and self.clear_bend(corner, first_heading, last_heading, obstacles)
                    ):
                        return start, corner, finish
                elif abs(delta[middle_axis]) >= 2 * self.radius:
                    middle_heading = 2 * middle_axis + (delta[middle_axis] < 0)
                    other = _shift(corner, _HEADINGS[middle_heading], abs(delta[middle_axis]))
                    if (
                        self.clear(_straight_box(start, corner, self.margin), obstacles)
                        and self.clear(_straight_box(corner, other, self.margin), obstacles)
                        and self.clear(_straight_box(other, finish, self.margin), obstacles)
                        and self.clear_bend(corner, first_heading, middle_heading, obstacles)
                        and self.clear_bend(other, middle_heading, last_heading, obstacles)
                    ):
                        return start, corner, other, finish
        if not junctions:
            # Escape both real mouths before crossing a congested production
            # floor. Obstacle faces supply the intermediate plane; the coarse
            # routing grid alone can miss the gap below the next support slab.
            grid = self.registry.limits.hologram_grid_cm
            assert grid is not None  # The constructor requires the native grid.
            lead = math.ceil(2 * self.radius / grid) * grid
            a = _shift(start, _HEADINGS[first_heading], lead)
            b = _shift(finish, _HEADINGS[last_heading], -lead)
            for axis in range(3):
                if axis in (first_heading // 2, last_heading // 2):
                    continue
                planes = {
                    a[axis] - 2 * self.radius, a[axis] + 2 * self.radius,
                    b[axis] - 2 * self.radius, b[axis] + 2 * self.radius,
                    *self.grid[axis],
                    *(bound[axis] + sign * (self.margin + 2 * _EPS)
                      for _, low, high in obstacles
                      for bound, sign in ((low, -1), (high, 1))),
                }
                for plane in sorted(planes, key=lambda v: abs(v-a[axis]) + abs(v-b[axis])):
                    self.check()
                    if (
                        axis == 2 and self.max_unpumped_z is not None
                        and plane > self.max_unpumped_z
                    ):
                        continue
                    for order in permutations(i for i in range(3) if i != axis):
                        waypoints = [start, a]
                        point = list(a)
                        point[axis] = plane
                        waypoints.append((point[0], point[1], point[2]))
                        for i in order:
                            point[i] = b[i]
                            waypoints.append((point[0], point[1], point[2]))
                        waypoints.extend((b, finish))
                        path = tuple(
                            p for i, p in enumerate(waypoints)
                            if not i or math.dist(p, waypoints[i-1]) > _EPS
                        )
                        if (
                            self.max_unpumped_z is not None
                            and any(p[2] > self.max_unpumped_z + _EPS for p in path)
                        ):
                            continue
                        if any(
                            math.dist(p, q) < self.radius - _EPS
                            for p, q in zip(path, path[1:], strict=False)
                        ):
                            continue
                        headings = [
                            _heading((q[0]-p[0], q[1]-p[1], q[2]-p[2]))
                            for p, q in zip(path, path[1:], strict=False)
                        ]
                        if any(
                            h // 2 == headings[i+1] // 2
                            for i, h in enumerate(headings[:-1])
                        ):
                            continue
                        if any(
                            math.dist(p, q) < (self.radius if i in (0, len(headings)-1)
                                              else 2 * self.radius) - _EPS
                            for i, (p, q) in enumerate(zip(path, path[1:], strict=False))
                        ):
                            continue
                        if all(
                            self.clear(_straight_box(p, q, self.margin), obstacles)
                            for p, q in zip(path, path[1:], strict=False)
                        ) and all(
                            self.clear_bend(path[i+1], h, headings[i+1], obstacles)
                            for i, h in enumerate(headings[:-1])
                        ):
                            return path
        radius = self.junction_radius if junctions else self.radius
        minimum_pipe = min(
            definition.mesh_length_cm / 2
            for _, definition in self.definitions
            if definition.mesh_length_cm is not None
        )
        # Terminal legs need one bend radius; interior legs need two. Omitting
        # the one-radius candidates can strand a legal mouth beside a wall.
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
                                start[i] + multiple * radius
                                for multiple in (-2, -1, 1, 2)
                                if (self.wall_margin if i == 2 else -self.half + self.wall_margin)
                                <= start[i] + multiple * radius
                                <= (self.height if i == 2 else self.half) - self.wall_margin
                            ],
                            *[
                                finish[i] + multiple * radius
                                for multiple in (-2, -1, 1, 2)
                                if (self.wall_margin if i == 2 else -self.half + self.wall_margin)
                                <= finish[i] + multiple * radius
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
                reverse_path = [here]
                while state in parents:
                    state = parents[state]
                    reverse_path.append(position(state[0]))
                return tuple(reversed(reverse_path))
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
                        if not self.clear_bend(here, arrival, direction, obstacles):
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
            # One planar corner is already a complete native-radius route.
            # Multi-axis climbs and reversals may still benefit from fittings.
            planar = sum(abs(start[i] - finish[i]) > _EPS for i in range(3)) <= 2
            corner_axes = _heading(facing) // 2 != _heading(normal) // 2
            prefer_junctions = (
                abs(start[2] - finish[2]) > _EPS
                and math.dist(start[:2], finish[:2]) > _EPS
                and not planar
                and not corner_axes
            ) or _heading(facing) == _heading(normal)
            if not prefer_junctions:
                try:
                    curved = self.search(source, target)
                except SectionError as exc:
                    if exc.cause != "routing":
                        raise
                    prefer_junctions = True
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

    def branch_node(
        self, terminal: _Ref, item: str, rate: Fraction, rotation: Pose,
        node_ceiling: float | None = None,
    ) -> PipeAttachmentObj:
        definition = self.registry.buildables[_JUNCTION]
        branch = _pipe_port(definition, (0, 1, 0))
        offset = world_port(rotation.transform(), branch)
        facing = port_forward(rotation.transform(), branch)
        at, normal = self.at(terminal)
        # Fittings are continuous native geometry, not conveyor lattice nodes.
        # Include the terminal's transverse/height phases and a forward native
        # connector span, retaining usable bays in narrow outer corridors.
        axes = tuple(
            tuple(sorted({
                *self.grid[i], at[i], at[i] - offset[i],
                at[i] + normal[i] * (self.radius + 2 * _EPS),
                at[i] + normal[i] * (2 * self.radius + 2 * _EPS),
                at[i] + normal[i] * (self.junction_radius + 2 * self.radius),
                at[i] + normal[i] * (3 * self.junction_radius + 2 * self.radius),
            }))
            for i in range(3)
        )

        def proximity(point: Vector) -> tuple[int, float]:
            # Prefer forward endpoint headings with one or two monotone
            # corners, not the nearest wrong-facing mouth requiring a reversal.
            first_axis = _heading(normal) // 2
            last_axis = _heading(facing) // 2
            finish = tuple(point[i] + offset[i] for i in range(3))
            rank = 3
            if first_axis == last_axis:
                transverse = all(
                    abs(finish[i] - at[i]) <= _EPS for i in range(3) if i != first_axis
                )
                if (
                    transverse and normal[first_axis] * facing[first_axis] < -0.999
                    and (finish[first_axis] - at[first_axis]) * normal[first_axis] > 0
                ):
                    rank = 0
            else:
                middle_axis = 3 - first_axis - last_axis
                first = (finish[first_axis] - at[first_axis]) * normal[first_axis]
                last = (at[last_axis] - finish[last_axis]) * facing[last_axis]
                middle = abs(finish[middle_axis] - at[middle_axis])
                if first >= self.radius and last >= self.radius:
                    if middle <= _EPS:
                        rank = 1
                    elif middle >= 2 * self.radius:
                        rank = 2
            return rank, sum(abs(point[i] - at[i]) for i in range(3))

        candidates = sorted(
            ((x, y, z) for x, y, z in product(*axes)), key=proximity
        )
        for point in candidates:
            self.check()
            ceiling = node_ceiling if node_ceiling is not None else self.max_unpumped_z
            if ceiling is not None and point[2] > ceiling + _EPS:
                continue
            node = PipeAttachmentObj(
                next(self.ids), _JUNCTION,
                Pose(point[0], point[1], point[2],
                     rotation.yaw_deg, rotation.pitch_deg, rotation.roll_deg),
            )
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
        producer_caps = {
            (port.object_id, port.port): endpoint(
                placement, port.object_id, port.port, registry
            )[0][2]
            for section in sections
            for port in section.outputs
            if port.kind == "pipe" and port.item_id == item
        }
        # Each source can descend from its own actual outlet elevation, but
        # the shared gravity collector stays below every producer. No factory
        # pressure or uphill head is inferred from a lower collector.
        collector_ceiling = min(producer_caps.values()) if producer_caps else None
        routes.max_unpumped_z = collector_ceiling
        sources = sum((p.items_per_second for p in ports if p.direction == "output"), Fraction())
        sinks = sum((p.items_per_second for p in ports if p.direction == "input"), Fraction())
        if not sources or not sinks or sources != sinks:
            raise SectionError(
                f"{item}: fluid section rates do not balance ({sources} in, {sinks} out)",
                cause="balance",
            )
        if len({(p.object_id, p.port) for p in ports}) != len(ports):
            raise SectionError(f"{item}: duplicate pipe interface", cause="balance")
        by_ref = {(port.object_id, port.port): port for port in ports}
        paired: set[tuple[int, str]] = set()
        for boundary in ports:
            if boundary.peer is None:
                continue
            peer = by_ref.get(boundary.peer)
            if (
                peer is None or peer.direction == boundary.direction
                or peer.items_per_second != boundary.items_per_second
                or boundary.peer in paired
            ):
                raise SectionError(
                    f"{item}: invalid allocated native stack takeoff", cause="balance"
                )
            routes.max_unpumped_z = producer_caps.get(boundary.peer)
            routes.connect(
                boundary.peer, (boundary.object_id, boundary.port), item,
                boundary.items_per_second,
            )
            paired.update((boundary.peer, (boundary.object_id, boundary.port)))
        ports = [port for port in ports if (port.object_id, port.port) not in paired]
        if not ports:
            continue
        if len(ports) == 2:
            routes.connect(
                (ports[0].object_id, ports[0].port),
                (ports[1].object_id, ports[1].port),
                item,
                sources,
            )
            continue
        positions = [
            endpoint(placement, port.object_id, port.port, registry)[0] for port in ports
        ]
        axis = max(
            range(2 if producer_caps else 3),
            key=lambda i: max(point[i] for point in positions) - min(
                point[i] for point in positions
            )
        )
        rotation = (
            Pose(0, 0, 0, 0) if axis == 0
            else Pose(0, 0, 0, 90) if axis == 1
            else Pose(0, 0, 0, 0, 90)
        )
        ordered = sorted(zip(ports, positions, strict=True), key=lambda entry: entry[1][axis])
        ports = [port for port, _ in ordered]
        nodes = []
        for port in ports:
            routes.max_unpumped_z = producer_caps.get(
                (port.object_id, port.port), collector_ceiling
            )
            nodes.append(routes.branch_node(
                (port.object_id, port.port), item, port.items_per_second,
                rotation, collector_ceiling,
            ))
        routes.max_unpumped_z = collector_ceiling
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
