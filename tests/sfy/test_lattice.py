"""The node lattice a grid-routed belt is searched on, and what is in the way.

Every geometric claim here is checked twice: once against
:mod:`flab2bp.sfy.layout.lattice`, which says which nodes a belt may pass, and
once against :mod:`flab2bp.sfy.layout.validate`, which judges the belts that
come out of it.  A lattice that says two belts fit where the validator says they
lap is a router that produces builds this project refuses, so the two are held
against each other rather than each against a number written here.
"""

from __future__ import annotations

import ast
import math
import subprocess
import sys
from fractions import Fraction
from functools import cache
from itertools import product

import pytest

from flab2bp.sfy.geometry import world_port
from flab2bp.sfy.layout.corridors import attachment_box_cm
from flab2bp.sfy.layout.lattice import (
    GROUND_LEVEL,
    Lattice,
    Node,
    Occupancy,
    belt_levels,
    occupancy_for,
)
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    FoundationObj,
    LiftObj,
    MachineObj,
    Pose,
    SfyPlacement,
    Vector,
    lift_geometry,
)
from flab2bp.sfy.layout.splines import straight
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.registry import Port, Registry, load_registry
from flab2bp.sfy.spec import designer

CONSTRUCTOR = "Build_ConstructorMk1_C"
SPLITTER = "Build_ConveyorAttachmentSplitter_C"
BELT = "Build_ConveyorBeltMk1_C"
LIFT = "Build_ConveyorLiftMk1_C"
ROD = "Recipe_IronRod_C"

#: The machine's own floor: a machine stands on the slab, whose top is at 100.
SLAB_TOP_CM = 100.0
#: A grid-snapped machine's belt ports sit here -- slab top plus the port's own
#: ``translation[2]`` of 100 -- which is lattice level 2 (R-M3-1).
PORT_Z_CM = 200.0


@cache
def _registry() -> Registry:
    return load_registry()


@cache
def _lattice(mark: str = "mk1") -> Lattice:
    return Lattice.over(designer(mark, _registry()), _registry())


def _port(class_name: str, port_name: str) -> Port:
    return next(p for p in _registry().buildables[class_name].ports if p.name == port_name)


def _constructor(id: int = 1, x: float = 0.0, y: float = 0.0) -> MachineObj:
    """A Constructor standing on the slab, centred on a lattice node."""
    return MachineObj(id, CONSTRUCTOR, Pose(x, y, SLAB_TOP_CM, 0.0), ROD)


def _empty() -> Occupancy:
    """The designer with nothing in it: only the walls and the floor block."""
    return occupancy_for(_lattice(), (), (), (), (), _registry())


def _belt(id: int, start: Vector, facing: Vector, length: float) -> BeltRun:
    return BeltRun(id, BELT, straight(start, facing, length), "iron-rod", Fraction(1, 4))


def _capsule_errors(*belts: BeltRun, machines: tuple[MachineObj, ...] = ()) -> list[str]:
    """What ``belt.capsule`` says about these belts, on their own."""
    placement = SfyPlacement(designer=designer("mk1", _registry()), machines=machines, belts=belts)
    report = validate(placement, None, _registry(), only={"belt.capsule"})
    return [f.message for f in report.errors]


# --- the lattice itself ----------------------------------------------------


@pytest.mark.parametrize("mark", ["mk1", "mk2", "mk3"])
def test_node_and_world_are_inverses_on_every_mark(mark: str) -> None:
    """R-M3-1, both ways round, on all three designers.

    The node grid is the hologram's own 1 m grid over the designer's own volume:
    ``dims`` foundations of ``foundation_cm`` each, so ``n`` is ``dims * 8`` and
    nothing here is a number this test chose.
    """
    board = designer(mark, _registry())
    lattice = _lattice(mark)
    assert lattice.grid_cm == _registry().limits.hologram_grid_cm
    assert lattice.n == board.dims[0] * 8
    assert lattice.size() == (lattice.n + 1) ** 3

    n, half = lattice.n, board.half_cm
    assert lattice.world((0, 0, 0)) == (-half, -half, 0.0)
    assert lattice.world((n, n, n)) == (half, half, board.height_cm)
    for node in ((0, 0, 0), (n, n, n), (1, 2, 3), (n // 2, n // 3, 2)):
        assert lattice.node(lattice.world(node)) == node
        assert lattice.level(lattice.world(node)[2]) == node[2]
        assert lattice.index(node) == lattice.codec.encode(node)

    assert lattice.node((0.0, 0.0, 50.0)) is None  # between two levels
    assert lattice.node((half + 100.0, 0.0, 0.0)) is None  # outside the designer
    assert lattice.level(250.0) is None
    assert lattice.level(-100.0) is None
    with pytest.raises(IndexError):
        lattice.index((n + 1, 0, 0))


def test_levels_zero_and_one_are_never_passable() -> None:
    """R-M3-3: the slab, and the band between the slab and the ports.

    Nothing is standing in this designer at all, so these two levels are refused
    for what they ARE and not for what is in them.
    """
    occupancy = _empty()
    lattice = _lattice()
    assert GROUND_LEVEL == 2
    for i, j in product(range(lattice.n + 1), repeat=2):
        assert not occupancy.free((i, j, 0))
        assert not occupancy.free((i, j, 1))
    assert occupancy.free((1, 1, GROUND_LEVEL))


def test_an_upper_floor_blocks_routes_without_closing_space_above_and_below() -> None:
    lattice = _lattice()
    slab = FoundationObj(91, "Build_Foundation_8x1_01_C", Pose(0, 0, 1050, 0))
    occupancy = occupancy_for(lattice, (), (), (), (), _registry(), foundations=(slab,))
    for z, expected in ((900, True), (1000, False), (1100, False), (1200, True)):
        node = lattice.node((0, 0, z))
        assert node is not None
        assert occupancy.free(node) is expected


def test_the_wall_lines_and_the_ceiling_are_never_passable() -> None:
    """A belt centreline on the wall reaches 79 cm outside it -- ``geom.bounds``.

    The designer's volume is ``[-half, half]^2 x [0, height]`` and a belt's
    clearance box is 79 cm each way across and 15 cm up; a centreline on the
    outermost line or the topmost level hangs its own box out of the designer,
    which is exactly what ``geom.bounds`` refuses.
    """
    occupancy = _empty()
    n = _lattice().n
    for k in range(GROUND_LEVEL, n + 1):
        assert not occupancy.free((0, 5, k))
        assert not occupancy.free((n, 5, k))
        assert not occupancy.free((5, 0, k))
        assert not occupancy.free((5, n, k))
    for i, j in product(range(n + 1), repeat=2):
        assert not occupancy.free((i, j, n))
    assert occupancy.free((1, 1, n - 1))


@pytest.mark.parametrize("mark", ["mk1", "mk2", "mk3"])
def test_object_lines_allow_exact_attachment_wall_clearance(mark: str) -> None:
    """A box may touch the wall; adding the belt margin would waste a line."""
    lattice = _lattice(mark)
    half = attachment_box_cm(_registry())
    inset = math.ceil(half / lattice.grid_cm)
    lines = lattice.object_lines
    assert lines == range(inset, lattice.n - inset + 1)
    occupancy = occupancy_for(lattice, (), (), (), (), _registry())
    for line in (lattice.open_lines.start, lattice.open_lines.stop - 1):
        assert line not in lines
        assert occupancy.free((line, lattice.n // 2, GROUND_LEVEL))
    for line in (lines.start, lines.stop - 1):
        x = lattice.world((line, lattice.n // 2, GROUND_LEVEL))[0]
        assert -lattice.designer.half_cm <= x - half
        assert x + half <= lattice.designer.half_cm


def test_a_node_only_lattice_needs_no_attachment_measurement() -> None:
    lattice = Lattice(designer("mk1", _registry()), _lattice().grid_cm)
    node = (lattice.open_lines.start, lattice.n // 2, GROUND_LEVEL)
    assert lattice.node(lattice.world(node)) == node
    assert lattice.object_lines == lattice.open_lines


# --- what a placed object takes --------------------------------------------


def test_a_constructor_blocks_levels_one_to_seven_and_four_lines_out() -> None:
    """R-M3-2 over the game's own box, in all three axes.

    A Constructor's hard box is ``min (-400, -500, 0)``, ``max (400, 500, 600)``
    and it stands at ``z = 100``, so it spans ``z 100..700``.  A belt box is
    79 cm each way across and 15 cm up, so the lines it denies are ``|dx| <= 4``
    (500 cm out clears 400 by 21) and ``|dy| <= 5``, and the levels are 1 to 7
    -- level 7's box runs 685..715 and meets the box's own top at 700, level 8's
    runs 785..815 and does not.
    """
    assert belt_levels(SLAB_TOP_CM, 700.0, 100.0) == range(1, 8)

    lattice = _lattice()
    occupancy = occupancy_for(lattice, (_constructor(),), (), (), (), _registry())
    i0 = j0 = lattice.n // 2
    assert lattice.world((i0, j0, GROUND_LEVEL))[:2] == (0.0, 0.0)

    for k in range(GROUND_LEVEL, 8):
        for dx, dy in product(range(-4, 5), range(-5, 6)):
            assert not occupancy.free((i0 + dx, j0 + dy, k)), (dx, dy, k)
        assert occupancy.free((i0 + 5, j0, k))
        assert occupancy.free((i0 - 5, j0, k))
        assert occupancy.free((i0, j0 + 6, k))
        assert occupancy.free((i0, j0 - 6, k))
    for dx, dy in product(range(-4, 5), range(-5, 6)):
        assert occupancy.free((i0 + dx, j0 + dy, 8)), (dx, dy)


def test_a_belt_runs_beside_a_machine_at_port_height_one_line_outside_its_box() -> None:
    """The line the lattice opens is the line the validator accepts, and no closer.

    Line 5 out in ``X`` is 500 cm from a Constructor's centre, 100 from its box
    face: the belt's own box reaches 421 and clears by 21 cm.  Line 4 is 400,
    flush with the face, and laps by 79.  Both answers are checked against
    ``belt.capsule`` on the real spline, because a lattice that disagrees with
    the judge is a router that builds refused belts.
    """
    lattice = _lattice()
    machine = _constructor()
    occupancy = occupancy_for(lattice, (machine,), (), (), (), _registry())
    i0 = j0 = lattice.n // 2

    for dy in range(-5, 6):
        assert occupancy.free((i0 + 5, j0 + dy, GROUND_LEVEL))
        assert not occupancy.free((i0 + 4, j0 + dy, GROUND_LEVEL))

    clear = _belt(20, (500.0, -500.0, PORT_Z_CM), (0.0, 1.0, 0.0), 1000.0)
    flush = _belt(21, (400.0, -500.0, PORT_Z_CM), (0.0, 1.0, 0.0), 1000.0)
    assert _capsule_errors(clear, machines=(machine,)) == []
    assert _capsule_errors(flush, machines=(machine,)) != []


def test_a_lift_column_blocks_its_levels_between_the_ends() -> None:
    """``lift.clearance``'s one box, flattened: 95 cm each way and the span.

    A 400 cm lift standing at ``z = 200`` runs to 600, and its box is the
    game's own half-extent each way across.  So it denies the node it stands on
    and its four neighbours' lines -- 100 cm out, inside 95 + 79 -- over levels
    2 to 6, and nothing at level 7 or two lines out.
    """
    lattice = _lattice()
    lift = LiftObj(3, LIFT, Pose(0.0, 0.0, PORT_Z_CM, 0.0), 400.0)
    bottom, _ = lift.bottom_end(lift_geometry(_registry(), LIFT))
    top, _ = lift.top_end(lift_geometry(_registry(), LIFT))
    assert (bottom[2], top[2]) == (200.0, 600.0)

    occupancy = occupancy_for(lattice, (), (), (lift,), (), _registry())
    i0 = j0 = lattice.n // 2
    for k in range(GROUND_LEVEL, 7):
        for dx, dy in product(range(-1, 2), repeat=2):
            assert not occupancy.free((i0 + dx, j0 + dy, k)), (dx, dy, k)
        assert occupancy.free((i0 + 2, j0, k))
        assert occupancy.free((i0, j0 - 2, k))
    assert occupancy.free((i0, j0, 7))


# --- what a committed path takes -------------------------------------------


def test_a_committed_run_shadows_the_two_nodes_across_it_and_not_the_one_beyond_its_end() -> None:
    """R-M3-2's committed-run rule, and its three deliberate edges.

    Across the run: two belts one node apart lap by 58 cm, so the line either
    side goes.  Beyond the end: the run's box stops at its last node, so a
    perpendicular belt 100 cm past it reaches only to within 21 cm -- that node
    stays free, and only that node.  And one level up is another world.
    """
    lattice = _lattice()
    occupancy = _empty()
    path: list[Node] = [(10, 16, GROUND_LEVEL), (11, 16, GROUND_LEVEL), (12, 16, GROUND_LEVEL)]
    occupancy.commit(7, path)

    for i in (10, 11, 12):
        assert not occupancy.free((i, 16, GROUND_LEVEL))
        assert not occupancy.free((i, 15, GROUND_LEVEL))
        assert not occupancy.free((i, 17, GROUND_LEVEL))
        assert occupancy.owner[lattice.index((i, 15, GROUND_LEVEL))] == 7
        assert occupancy.free((i, 14, GROUND_LEVEL))
        assert occupancy.free((i, 16, GROUND_LEVEL + 1))
        assert occupancy.free((i, 16, GROUND_LEVEL - 1)) is False  # the slab band

    assert occupancy.free((13, 16, GROUND_LEVEL))  # beyond the end
    assert occupancy.free((9, 16, GROUND_LEVEL))  # before the start
    assert occupancy.free((13, 15, GROUND_LEVEL))  # and only that node

    taken = occupancy.snapshot()
    assert taken == bytes(occupancy.flags)
    occupancy.commit(8, [(4, 4, GROUND_LEVEL), (5, 4, GROUND_LEVEL)])
    assert taken != bytes(occupancy.flags)  # a snapshot is a copy, not a view


def test_two_belts_two_nodes_apart_are_both_free_and_validate_clean() -> None:
    """Belt pitch on this lattice is 200 cm, and 200 is really legal.

    158 is not a multiple of 100, so the lattice cannot offer the game's own
    closest pitch: the next line out is 100, which laps by 58, and the one after
    is 200, which clears by 42.  Both halves are checked on the real splines --
    the wide pair passes ``belt.capsule`` and the close pair does not -- so the
    stricter-than-the-game pitch is a rounding of the game's number and not a
    number of ours.
    """
    lattice = _lattice()
    occupancy = _empty()
    first: list[Node] = [(i, 14, GROUND_LEVEL) for i in range(8, 20)]
    occupancy.commit(1, first)
    assert all(occupancy.free((i, 16, GROUND_LEVEL)) for i in range(8, 20))
    assert not any(occupancy.free((i, 15, GROUND_LEVEL)) for i in range(8, 20))
    occupancy.commit(2, [(i, 16, GROUND_LEVEL) for i in range(8, 20)])

    y_apart = lattice.world((0, 14, 0))[1], lattice.world((0, 16, 0))[1]
    assert y_apart[1] - y_apart[0] == 200.0
    apart = [
        _belt(30 + k, (-800.0, y, PORT_Z_CM), (1.0, 0.0, 0.0), 1100.0)
        for k, y in enumerate(y_apart)
    ]
    close = [
        _belt(40 + k, (-800.0, y, PORT_Z_CM), (1.0, 0.0, 0.0), 1100.0)
        for k, y in enumerate((y_apart[0], y_apart[0] + 100.0))
    ]
    assert _capsule_errors(*apart) == []
    assert _capsule_errors(*close) != []


def test_a_belt_from_a_constructor_port_crosses_its_own_box_and_no_other_nets() -> None:
    """R-M3-2 (d): the nodes between a port and its machine's face stay blocked.

    A Constructor's ``Output0`` sits at ``(0, 300, 200)``, 200 cm inside a box
    that runs to ``y = 500``; the belt leaving it has to cross its own machine.
    Those nodes are blocked here like any other node in the box -- opening them
    is the port's own business, on Task 6's ``Terminal.reach`` and Task 5's
    ``opened`` -- and, because the world already denies them, a net that is
    committed across them never takes ownership of them.  If it did, a repair
    search could rip that net up to "free" a node the machine is standing in.
    """
    lattice = _lattice()
    machine = _constructor()
    occupancy = occupancy_for(lattice, (machine,), (), (), (), _registry())
    port = world_port(machine.pose.transform(), _port(CONSTRUCTOR, "Output0"))
    assert port == (0.0, 300.0, PORT_Z_CM)
    mouth = lattice.node(port)
    assert mouth is not None

    reach = [(mouth[0], j, GROUND_LEVEL) for j in range(mouth[1], mouth[1] + 3)]
    assert all(not occupancy.free(node) for node in reach)
    assert occupancy.free((mouth[0], mouth[1] + 3, GROUND_LEVEL))

    occupancy.commit(5, [*reach, (mouth[0], mouth[1] + 3, GROUND_LEVEL)])
    for node in reach:
        assert lattice.index(node) not in occupancy.owner
        assert not occupancy.free(node)
    assert occupancy.owner[lattice.index((mouth[0], mouth[1] + 3, GROUND_LEVEL))] == 5


def test_rip_up_restores_the_base_not_one() -> None:
    """A ripped node goes back to what the WORLD said, not to passable.

    The run here is one line clear of a Constructor, so its shadow falls on the
    machine's own outermost line on one side and on empty lattice on the other.
    Rip it up and the empty side comes back; the machine's side does not, and
    the machine is still standing there.
    """
    lattice = _lattice()
    machine = _constructor()
    occupancy = occupancy_for(lattice, (machine,), (), (), (), _registry())
    i0 = j0 = lattice.n // 2

    lane = [(i, j0 + 6, GROUND_LEVEL) for i in range(i0 - 2, i0 + 3)]
    inside = (i0, j0 + 5, GROUND_LEVEL)
    outside = (i0, j0 + 7, GROUND_LEVEL)
    assert not occupancy.free(inside)
    assert occupancy.free(outside)
    assert occupancy.base[lattice.index(inside)] == 0
    assert occupancy.base[lattice.index(outside)] == 1

    occupancy.commit(9, lane)
    assert not occupancy.free(outside)
    occupancy.rip_up(9)
    assert all(occupancy.free(node) for node in lane)
    assert occupancy.free(outside)
    assert not occupancy.free(inside)
    assert occupancy.owner == {}


def test_a_ripped_net_leaves_a_shadow_another_net_still_needs() -> None:
    """Two runs 200 cm apart shadow the line between them; one leaving keeps it.

    Both are legal and both deny the line between them, so the line has two
    owners.  Ripping one up must not hand that line to a third net, which would
    then be built 100 cm from a belt that is still there.
    """
    lattice = _lattice()
    occupancy = _empty()
    between = [(i, 15, GROUND_LEVEL) for i in range(8, 20)]
    occupancy.commit(1, [(i, 14, GROUND_LEVEL) for i in range(8, 20)])
    occupancy.commit(2, [(i, 16, GROUND_LEVEL) for i in range(8, 20)])
    assert all(occupancy.owner[lattice.index(node)] == 1 for node in between)

    occupancy.rip_up(1)
    assert all(not occupancy.free(node) for node in between)
    assert all(occupancy.owner[lattice.index(node)] == 2 for node in between)
    occupancy.rip_up(2)
    assert all(occupancy.free(node) for node in between)


def test_physical_claims_survive_interface_reservations_without_freeing_them_on_rip_up() -> None:
    lattice = _lattice()
    occupancy = _empty()
    reserved = (10, 10, 5)
    index = lattice.index(reserved)
    occupancy.flags[index] = 0
    occupancy.base = bytes(occupancy.flags)

    occupancy.commit(7, (reserved,), claim_blocked=True)
    occupancy.commit(8, (reserved,), claim_blocked=True)
    assert occupancy.owner[index] == 7
    assert not occupancy.free(reserved)
    occupancy.rip_up(7)
    assert occupancy.owner[index] == 8
    assert not occupancy.free(reserved)
    occupancy.rip_up(8)
    assert index not in occupancy.owner
    assert not occupancy.free(reserved)


# --- the invariant ---------------------------------------------------------


def test_flags_agree_with_free_on_every_node() -> None:
    """The flat array and the predicate must agree node for node.

    This is the DSP router's own hardest-won lesson: a ``free()`` that knows
    something the searched array does not is a search returning paths the
    committer then throws away, round after round, learning nothing.  So the
    whole lattice is walked over a placement with two machines, an attachment, a
    lift, a belt and a committed net in it.
    """
    lattice = _lattice()
    machines = (_constructor(1, -800.0, -800.0), _constructor(2, 800.0, 800.0))
    attachments = (AttachmentObj(4, SPLITTER, Pose(0.0, -400.0, PORT_Z_CM, 0.0)),)
    lifts = (LiftObj(5, LIFT, Pose(-400.0, 0.0, PORT_Z_CM, 0.0), 400.0),)
    belts = (_belt(6, (-800.0, 400.0, PORT_Z_CM), (1.0, 0.0, 0.0), 1000.0),)
    occupancy = occupancy_for(lattice, machines, attachments, lifts, belts, _registry())
    occupancy.commit(11, [(i, 20, GROUND_LEVEL + 1) for i in range(4, 12)])

    for i, j, k in product(range(lattice.n + 1), repeat=3):
        node: Node = (i, j, k)
        assert (occupancy.flags[lattice.index(node)] == 1) is occupancy.free(node), node


def test_an_attachment_denies_a_belt_nothing() -> None:
    """A splitter's only clearance box is SOFT, so it blocks no belt.

    The hologram's own marking decides this: ``CT_Soft`` is a box that may be
    shared, and ``geom.hard_clearance`` tests only the hard ones.  The lattice
    takes an attachment's boxes through exactly the same filter as a machine's,
    so a class that ever gains a hard one is blocked without a change here.
    """
    lattice = _lattice()
    splitter = AttachmentObj(4, SPLITTER, Pose(0.0, 0.0, PORT_Z_CM, 0.0))
    with_it = occupancy_for(lattice, (), (splitter,), (), (), _registry())
    assert with_it.flags == _empty().flags


def test_importing_the_lattice_loads_none_of_the_dsp_placer() -> None:
    """The one DSP module the lattice adds is the flat index codec, and no other.

    ``geometric_world.GridIndex`` is THE place the routing grid's layout is
    written and it is deliberately dependency-free -- its only reference to the
    placer is under ``TYPE_CHECKING``.  So importing the lattice must cost
    nothing of ``routing_domain``, the router, the kernel, the packers or the
    finaliser.  The baseline is the validator, which the lattice imports anyway
    and which already pulls in the shared ``layout.base`` types, so what this
    measures is the lattice's OWN reach into the DSP placer.
    """
    code = (
        "import sys; import flab2bp.sfy.layout.validate;"
        "before = {n for n in sys.modules if n.startswith('flab2bp.layout')};"
        "import flab2bp.sfy.layout.lattice;"
        "after = {n for n in sys.modules if n.startswith('flab2bp.layout')};"
        "print(sorted(after - before))"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert ast.literal_eval(out.stdout.strip()) == ["flab2bp.layout.geometric_world"]
