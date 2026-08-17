"""Layout and SVG rendering."""

import re
import xml.etree.ElementTree as ET

import pytest

from pid.examples import bleaching_unit
from pid.layout import Route, compute_layout
from pid.model import Endpoint, Equipment, PIDModel, Port, Stream
from pid.render import _Occupancy, _horizontal_runs, render_svg
from pid.symbols import EQUIPMENT_SYMBOLS, equipment_symbol, inline_symbol
from pid.standards import INLINE_TYPES, INSTRUMENT_LOCATIONS


@pytest.fixture()
def model():
    return bleaching_unit()


# -- layout -----------------------------------------------------------------


def test_every_equipment_item_is_placed(model):
    layout = compute_layout(model)
    assert set(layout.nodes) == {e.tag for e in model.equipment}


def test_every_line_is_routed(model):
    layout = compute_layout(model)
    assert len(layout.routes) == len(model.streams)
    for route in layout.routes:
        assert len(route.points) >= 2


def test_process_flow_runs_left_to_right(model):
    layout = compute_layout(model)
    # Each unit operation sits downstream of the one feeding it.
    order = ["TK-1201", "P-1201A", "E-1201", "V-1201", "P-1202", "F-1201", "F-1202"]
    ranks = [layout.node(tag).rank for tag in order]
    assert ranks == sorted(ranks)
    assert ranks[0] < ranks[-1]


def test_off_page_connectors_sit_at_the_sheet_edges(model):
    layout = compute_layout(model)
    process_ranks = [layout.node(e.tag).rank for e in model.process_equipment()]
    first, last = min(process_ranks), max(process_ranks)

    sources = ["OSBL-1201", "OSBL-1203", "OSBL-1205"]
    sinks = ["OSBL-1202", "OSBL-1204", "OSBL-1206", "OSBL-1207"]
    assert all(layout.node(tag).rank < first for tag in sources)
    assert all(layout.node(tag).rank > last for tag in sinks)


def test_symbols_do_not_overlap_each_other(model):
    layout = compute_layout(model)
    nodes = list(layout.nodes.values())
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            gap_x = a.left > b.right or b.left > a.right
            gap_y = a.top > b.bottom or b.top > a.bottom
            assert gap_x or gap_y, f"{a.tag} overlaps {b.tag}"


def test_headroom_is_reserved_above_instrumented_equipment(model):
    layout = compute_layout(model)
    # V-1201 carries five bubbles, TK-1201's spare has none.
    assert layout.node("V-1201").headroom > 0
    assert layout.node("OSBL-1201").headroom == 0


def test_layout_is_deterministic(model):
    first = compute_layout(model)
    second = compute_layout(bleaching_unit())
    assert {t: (n.cx, n.cy) for t, n in first.nodes.items()} == {
        t: (n.cx, n.cy) for t, n in second.nodes.items()
    }


def test_a_recycle_does_not_hang_the_ranker():
    # Longest-path ranking by relaxation must terminate on a cycle.
    model = PIDModel(
        title="Recycle loop",
        equipment=[
            Equipment(tag="V-1", name="A", kind="vessel", ports=[Port(name="N1", kind="inlet"), Port(name="N2", kind="outlet")]),
            Equipment(tag="V-2", name="B", kind="vessel", ports=[Port(name="N1", kind="inlet"), Port(name="N2", kind="outlet")]),
        ],
        streams=[
            Stream(number='2"-PL-1-CS150', source=Endpoint(equipment="V-1", port="N2"), destination=Endpoint(equipment="V-2", port="N1"), service="PL", size_in=2.0),
            Stream(number='2"-PL-2-CS150', source=Endpoint(equipment="V-2", port="N2"), destination=Endpoint(equipment="V-1", port="N1"), service="PL", size_in=2.0),
        ],
    )
    layout = compute_layout(model)
    assert len(layout.routes) == 2
    # Which leg ends up as the back edge is arbitrary on a cycle — relaxation
    # settles on a stable but not meaningful order. What matters is that it
    # terminates and that exactly one leg is routed the long way round rather
    # than straight back through the symbols.
    point_counts = sorted(len(route.points) for route in layout.routes)
    assert point_counts[0] <= 4 and point_counts[1] > 4


def test_route_arc_length_helpers():
    route = Route(stream=None, points=[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)])
    assert route.length() == pytest.approx(200.0)
    assert route.point_at(0.0) == (0.0, 0.0)
    assert route.point_at(0.25) == pytest.approx((50.0, 0.0))
    assert route.point_at(0.75) == pytest.approx((100.0, 50.0))
    assert route.point_at(1.0) == (100.0, 100.0)
    assert route.angle_at(0.25) == pytest.approx(0.0)
    assert route.angle_at(0.75) == pytest.approx(90.0)


def test_horizontal_runs_are_returned_longest_first():
    runs = _horizontal_runs([(0.0, 0.0), (30.0, 0.0), (30.0, 10.0), (130.0, 10.0)])
    assert [round(length) for length, _, _ in runs] == [100, 30]


# -- occupancy --------------------------------------------------------------


def test_occupancy_prefers_the_first_clear_candidate():
    space = _Occupancy()
    space.add(0.0, 0.0, 20.0, 20.0)
    # First candidate collides, second does not.
    assert space.place([(0.0, 0.0), (100.0, 0.0)], 10.0, 10.0) == (100.0, 0.0)


def test_occupancy_falls_back_to_the_least_crowded_spot():
    space = _Occupancy()
    space.add(0.0, 0.0, 100.0, 100.0)  # big blocker
    space.add(200.0, 0.0, 4.0, 4.0)  # small blocker
    chosen = space.place([(0.0, 0.0), (200.0, 0.0)], 10.0, 10.0)
    assert chosen == (200.0, 0.0)


def test_occupancy_reports_its_bounds():
    space = _Occupancy()
    space.add(50.0, 50.0, 20.0, 20.0)
    assert space.bounds() == (40.0, 40.0, 60.0, 60.0)
    assert _Occupancy().bounds() == (0.0, 0.0, 0.0, 0.0)


# -- symbols ----------------------------------------------------------------


def test_every_equipment_class_has_a_symbol():
    from pid.standards import EQUIPMENT_PREFIXES

    assert set(EQUIPMENT_PREFIXES) <= set(EQUIPMENT_SYMBOLS)


def test_an_unknown_equipment_class_still_draws_something():
    symbol = equipment_symbol("thingamajig")
    assert symbol.width > 0 and symbol.body


def test_every_inline_type_has_a_symbol():
    for kind in INLINE_TYPES:
        assert inline_symbol(kind).strip(), kind


def test_every_instrument_location_has_a_bubble():
    from pid.symbols import instrument_bubble

    for location in INSTRUMENT_LOCATIONS:
        assert instrument_bubble(location).strip(), location


# -- SVG output -------------------------------------------------------------


def test_output_is_well_formed_svg(model):
    root = ET.fromstring(render_svg(model))
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert float(root.get("width")) > 0
    assert float(root.get("height")) > 0


def test_every_tag_appears_on_the_drawing(model):
    svg = render_svg(model)
    for equipment in model.equipment:
        assert equipment.tag in svg, equipment.tag
    for stream in model.streams:
        assert stream.number in svg, stream.number
    for instrument in model.instruments:
        # Bubbles split the tag into letters and number, so check both halves.
        letters, number = instrument.tag.split("-")
        assert f">{letters}<" in svg and f">{number}<" in svg, instrument.tag


def test_the_title_block_carries_the_drawing_metadata(model):
    svg = render_svg(model)
    assert model.title in svg
    assert model.drawing_number in svg
    assert model.project in svg


def test_notes_and_assumptions_both_reach_the_sheet(model):
    svg = render_svg(model)
    assert "NOTES" in svg
    assert "Assumed:" in svg


def test_rendering_is_deterministic(model):
    assert render_svg(model) == render_svg(bleaching_unit())


def _absolute_points(svg: str) -> list[tuple[float, float]]:
    """Every text anchor and polyline vertex, in sheet coordinates.

    Symbol bodies are drawn around a local origin and positioned by a
    ``translate``, so raw coordinates in the markup are meaningless on their own
    — the transforms have to be accumulated first.
    """
    root = ET.fromstring(svg)
    points: list[tuple[float, float]] = []

    def walk(element, dx: float, dy: float) -> None:
        transform = element.get("transform", "")
        match = re.match(r"translate\((-?[\d.]+),(-?[\d.]+)\)", transform)
        if match:
            dx += float(match.group(1))
            dy += float(match.group(2))
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "text":
            points.append((float(element.get("x")) + dx, float(element.get("y")) + dy))
        elif tag in {"polyline", "polygon"}:
            for pair in element.get("points", "").split():
                x, y = pair.split(",")
                points.append((float(x) + dx, float(y) + dy))
        elif tag == "line":
            points.append((float(element.get("x1")) + dx, float(element.get("y1")) + dy))
            points.append((float(element.get("x2")) + dx, float(element.get("y2")) + dy))
        for child in element:
            walk(child, dx, dy)

    walk(root, 0.0, 0.0)
    return points


def test_nothing_is_drawn_outside_the_sheet(model):
    svg = render_svg(model)
    root = ET.fromstring(svg)
    width, height = float(root.get("width")), float(root.get("height"))
    for x, y in _absolute_points(svg):
        assert 0.0 <= x <= width, f"x={x} outside 0..{width}"
        assert 0.0 <= y <= height, f"y={y} outside 0..{height}"


def test_bubbles_pushed_above_the_sheet_shift_the_whole_drawing():
    # Ten instruments on one item stack far higher than the layout reserves, so
    # the renderer has to grow the sheet and shift the drawing down.
    from pid.model import Instrument

    model = PIDModel(
        title="Heavily instrumented vessel",
        equipment=[
            Equipment(
                tag="V-1201",
                name="Vessel",
                kind="vessel",
                design_pressure_psig=50.0,
                design_temp_f=250.0,
                ports=[Port(name="N1", kind="outlet")],
            ),
            Equipment(tag="OSBL-1", name="To storage", kind="boundary", ports=[Port(name="N1", kind="inlet")]),
        ],
        streams=[
            Stream(
                number='4"-PL-1201-CS150',
                source=Endpoint(equipment="V-1201", port="N1"),
                destination=Endpoint(equipment="OSBL-1", port="N1"),
                service="PL",
                size_in=4.0,
                spec="CS150",
                phase="liquid",
            )
        ],
        instruments=[
            Instrument(tag=f"PI-12{index:02d}", description="Gauge", attached_to="V-1201")
            for index in range(10)
        ],
    )
    svg = render_svg(model)
    root = ET.fromstring(svg)
    width, height = float(root.get("width")), float(root.get("height"))
    for x, y in _absolute_points(svg):
        assert 0.0 <= x <= width and 0.0 <= y <= height


def test_a_drawing_with_no_notes_still_renders(model):
    model.notes = []
    model.assumptions = []
    svg = render_svg(model)
    assert "NOTES" not in svg
    assert ET.fromstring(svg) is not None


def test_an_empty_drawing_renders_without_crashing():
    svg = render_svg(PIDModel(title="Nothing here yet"))
    assert "Nothing here yet" in svg
    assert ET.fromstring(svg) is not None


def test_utility_lines_are_drawn_lighter_than_process_lines(model):
    svg = render_svg(model)
    assert 'class="utility-line"' in svg  # steam, condensate, nitrogen
    assert 'class="process-line"' in svg


def test_control_loops_are_linked_from_controller_to_final_element(model):
    svg = render_svg(model)
    # One dashed link per loop whose controller and valve both got placed.
    assert svg.count('class="loop-link"') == len(model.loops)
