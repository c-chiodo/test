"""Deterministic layout of a P&ID: where every symbol and line goes.

The agents decide *what* is on the drawing; this module decides *where*, with
no model involved, so the same P&ID always renders identically. That matters
for review — a diff between two revisions should show process changes, not the
layout engine changing its mind.

The approach is a layered (Sugiyama-style) graph layout:

1. **Rank** each equipment item by how far downstream it is, so process flow
   runs left to right.
2. **Order** items within a rank by the average position of their neighbours,
   which pulls connected symbols into line and cuts crossings.
3. **Place** ranks into columns and rows sized to the symbols they hold,
   reserving headroom above each item for its instrument bubbles.
4. **Route** each line as an orthogonal polyline, fanning the connection points
   out along the symbol edges so parallel lines stay readable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .model import PIDModel, Stream
from .symbols import Symbol, equipment_symbol

COLUMN_GAP = 132.0
ROW_GAP = 74.0
MARGIN = 60.0
BUBBLE_BAND = 44.0
BUBBLES_PER_ROW = 3
BACK_EDGE_CLEARANCE = 34.0
ANCHOR_INSET = 20.0
MIN_ANCHOR_PITCH = 15.0

Point = tuple[float, float]


@dataclass
class Node:
    """A placed equipment symbol."""

    tag: str
    kind: str
    label: str
    symbol: Symbol
    rank: int = 0
    row: int = 0
    cx: float = 0.0
    cy: float = 0.0
    headroom: float = 0.0

    @property
    def width(self) -> float:
        return self.symbol.width

    @property
    def height(self) -> float:
        return self.symbol.height

    @property
    def left(self) -> float:
        return self.cx - self.width / 2

    @property
    def right(self) -> float:
        return self.cx + self.width / 2

    @property
    def top(self) -> float:
        return self.cy - self.height / 2

    @property
    def bottom(self) -> float:
        return self.cy + self.height / 2


@dataclass
class Route:
    """A routed line, with the polyline the renderer draws."""

    stream: Stream
    points: list[Point] = field(default_factory=list)

    def length(self) -> float:
        return sum(
            math.dist(self.points[i], self.points[i + 1]) for i in range(len(self.points) - 1)
        )

    def point_at(self, t: float) -> Point:
        """Point a fraction ``t`` of the way along the polyline by arc length."""
        total = self.length()
        if total == 0 or len(self.points) < 2:
            return self.points[0] if self.points else (0.0, 0.0)
        target = max(0.0, min(1.0, t)) * total
        travelled = 0.0
        for i in range(len(self.points) - 1):
            (x1, y1), (x2, y2) = self.points[i], self.points[i + 1]
            segment = math.dist((x1, y1), (x2, y2))
            if travelled + segment >= target:
                frac = 0.0 if segment == 0 else (target - travelled) / segment
                return (x1 + (x2 - x1) * frac, y1 + (y2 - y1) * frac)
            travelled += segment
        return self.points[-1]

    def angle_at(self, t: float) -> float:
        """Direction of travel in degrees at fraction ``t``, for rotating symbols."""
        total = self.length()
        if total == 0 or len(self.points) < 2:
            return 0.0
        target = max(0.0, min(1.0, t)) * total
        travelled = 0.0
        for i in range(len(self.points) - 1):
            (x1, y1), (x2, y2) = self.points[i], self.points[i + 1]
            segment = math.dist((x1, y1), (x2, y2))
            if travelled + segment >= target or i == len(self.points) - 2:
                return math.degrees(math.atan2(y2 - y1, x2 - x1))
            travelled += segment
        return 0.0

    def midpoint(self) -> Point:
        return self.point_at(0.5)


@dataclass
class Layout:
    nodes: dict[str, Node] = field(default_factory=dict)
    routes: list[Route] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0

    def node(self, tag: str) -> Node | None:
        return self.nodes.get(tag)

    def route_for(self, line_number: str) -> Route | None:
        return next((r for r in self.routes if r.stream.number == line_number), None)


# ---------------------------------------------------------------------------
# Step 1 — ranking
# ---------------------------------------------------------------------------


def _rank_nodes(tags: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Longest-path ranking by relaxation.

    Relaxation rather than a topological sort because real P&IDs contain
    recycles. Capping the pass count at the node count makes a cycle settle on
    a stable (if arbitrary) ranking instead of looping forever.
    """
    rank = dict.fromkeys(tags, 0)
    for _ in range(len(tags)):
        changed = False
        for source, destination in edges:
            if source not in rank or destination not in rank:
                continue
            if rank[destination] < rank[source] + 1:
                rank[destination] = rank[source] + 1
                changed = True
        if not changed:
            break
    return rank


# ---------------------------------------------------------------------------
# Step 2 — ordering within ranks
# ---------------------------------------------------------------------------


def _pin_boundaries(
    ranks: dict[str, int], nodes: dict[str, Node], edges: list[tuple[str, str]]
) -> None:
    """Push off-page connectors out to the sheet edges.

    Real P&IDs put off-page connectors at the border, not in the middle of the
    process. Ranking alone strands them wherever their one connection happens to
    fall — a vent header fed from the second unit operation lands in the middle
    of the sheet with relief lines crossing everything to reach it.
    """
    has_outgoing = {source for source, _ in edges}
    has_incoming = {destination for _, destination in edges}

    process_ranks = [rank for tag, rank in ranks.items() if nodes[tag].kind != "boundary"]
    if not process_ranks:
        return
    first, last = min(process_ranks), max(process_ranks)

    for tag, node in nodes.items():
        if node.kind != "boundary":
            continue
        source_only = tag in has_outgoing and tag not in has_incoming
        sink_only = tag in has_incoming and tag not in has_outgoing
        if source_only:
            ranks[tag] = first - 1
        elif sink_only:
            ranks[tag] = last + 1


def _order_within_ranks(
    ranks: dict[str, int], edges: list[tuple[str, str]], order: list[str]
) -> dict[str, int]:
    """Assign a row to each node, pulling connected nodes towards each other."""
    by_rank: dict[int, list[str]] = {}
    for tag in order:
        by_rank.setdefault(ranks[tag], []).append(tag)

    rows = {tag: float(i) for tags in by_rank.values() for i, tag in enumerate(tags)}

    predecessors: dict[str, list[str]] = {tag: [] for tag in order}
    successors: dict[str, list[str]] = {tag: [] for tag in order}
    for source, destination in edges:
        if source in successors and destination in predecessors:
            successors[source].append(destination)
            predecessors[destination].append(source)

    def sweep(neighbours: dict[str, list[str]], rank_order: list[int]) -> None:
        for rank in rank_order:
            tags = by_rank[rank]
            keyed = []
            for index, tag in enumerate(tags):
                relevant = [rows[n] for n in neighbours[tag] if ranks[n] != rank]
                barycentre = sum(relevant) / len(relevant) if relevant else rows[tag]
                keyed.append((barycentre, index, tag))
            keyed.sort()
            for new_row, (_, _, tag) in enumerate(keyed):
                rows[tag] = float(new_row)
            by_rank[rank] = [tag for _, _, tag in keyed]

    ascending = sorted(by_rank)
    for _ in range(2):
        sweep(predecessors, ascending)
        sweep(successors, list(reversed(ascending)))

    return {tag: int(row) for tag, row in rows.items()}


# ---------------------------------------------------------------------------
# Step 3 — placement
# ---------------------------------------------------------------------------


def _place(nodes: dict[str, Node]) -> tuple[float, float]:
    """Turn (rank, row) into pixel centres. Returns the canvas size."""
    ranks = sorted({n.rank for n in nodes.values()})
    rows = sorted({n.row for n in nodes.values()})

    column_width = {
        rank: max((n.width for n in nodes.values() if n.rank == rank), default=0.0)
        for rank in ranks
    }
    row_height = {
        row: max(
            (n.height + n.headroom for n in nodes.values() if n.row == row),
            default=0.0,
        )
        for row in rows
    }

    column_x: dict[int, float] = {}
    cursor = MARGIN
    for rank in ranks:
        column_x[rank] = cursor + column_width[rank] / 2
        cursor += column_width[rank] + COLUMN_GAP

    row_y: dict[int, float] = {}
    cursor_y = MARGIN
    for row in rows:
        row_y[row] = cursor_y
        cursor_y += row_height[row] + ROW_GAP

    for node in nodes.values():
        node.cx = column_x[node.rank]
        # Sit the symbol at the bottom of its row band so the reserved
        # headroom above it is free for instrument bubbles.
        band_height = row_height[node.row]
        node.cy = row_y[node.row] + band_height - node.height / 2

    width = cursor - COLUMN_GAP + MARGIN
    height = cursor_y - ROW_GAP + MARGIN
    return width, height


# ---------------------------------------------------------------------------
# Step 4 — routing
# ---------------------------------------------------------------------------


def _spread(index: int, count: int, extent: float) -> float:
    """Offset of connection ``index`` of ``count`` along an edge of ``extent``.

    Small symbols get a fan wider than the symbol itself rather than three lines
    landing 6 px apart — a vent header taking four relief lines is unreadable
    otherwise.
    """
    if count <= 1:
        return 0.0
    usable = max(extent - ANCHOR_INSET, MIN_ANCHOR_PITCH * (count - 1))
    return -usable / 2 + usable * index / (count - 1)


def _route_streams(model: PIDModel, nodes: dict[str, Node]) -> list[Route]:
    outgoing: dict[str, list[Stream]] = {}
    incoming: dict[str, list[Stream]] = {}
    for stream in model.streams:
        outgoing.setdefault(stream.source.equipment, []).append(stream)
        incoming.setdefault(stream.destination.equipment, []).append(stream)

    # Fan connection points out in the order the lines actually leave, so
    # parallel runs do not cross immediately at the nozzle.
    def sort_key(stream: Stream, other_end: str):
        node = nodes.get(other_end)
        return (node.cy if node else 0.0, stream.number)

    for streams in outgoing.values():
        streams.sort(key=lambda s: sort_key(s, s.destination.equipment))
    for streams in incoming.values():
        streams.sort(key=lambda s: sort_key(s, s.source.equipment))

    routes: list[Route] = []
    back_edge_count = 0
    for stream in model.streams:
        source = nodes.get(stream.source.equipment)
        destination = nodes.get(stream.destination.equipment)
        if source is None or destination is None:
            continue  # dangling reference; rules.R002 reports it

        out_list = outgoing[stream.source.equipment]
        in_list = incoming[stream.destination.equipment]
        ay = source.cy + _spread(out_list.index(stream), len(out_list), source.height)
        by = destination.cy + _spread(in_list.index(stream), len(in_list), destination.height)
        ax, bx = source.right, destination.left

        if bx > ax + COLUMN_GAP * 0.4:
            if abs(ay - by) < 1.0:
                points = [(ax, ay), (bx, by)]
            else:
                mid = (ax + bx) / 2
                points = [(ax, ay), (mid, ay), (mid, by), (bx, by)]
        else:
            # Recycle or same-column line: drop below both symbols and run back.
            back_edge_count += 1
            depth = (
                max(source.bottom, destination.bottom)
                + BACK_EDGE_CLEARANCE
                + 14 * back_edge_count
            )
            exit_x = source.right + ANCHOR_INSET
            entry_x = destination.left - ANCHOR_INSET
            points = [
                (ax, ay),
                (exit_x, ay),
                (exit_x, depth),
                (entry_x, depth),
                (entry_x, by),
                (bx, by),
            ]
        routes.append(Route(stream=stream, points=points))
    return routes


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def compute_layout(model: PIDModel) -> Layout:
    """Place every symbol and route every line for ``model``."""
    nodes: dict[str, Node] = {}
    for equipment in model.equipment:
        symbol = equipment_symbol(equipment.kind)
        bubble_count = len(model.instruments_on(equipment.tag))
        bands = math.ceil(bubble_count / BUBBLES_PER_ROW) if bubble_count else 0
        nodes[equipment.tag] = Node(
            tag=equipment.tag,
            kind=equipment.kind,
            label=equipment.name,
            symbol=symbol,
            headroom=bands * BUBBLE_BAND,
        )

    order = [e.tag for e in model.equipment]
    edges = [
        (s.source.equipment, s.destination.equipment)
        for s in model.streams
        if s.source.equipment != s.destination.equipment
        and s.source.equipment in nodes
        and s.destination.equipment in nodes
    ]

    ranks = _rank_nodes(order, edges)
    _pin_boundaries(ranks, nodes, edges)
    # Ranks are used as dict keys and to size columns, so normalise them back to
    # a dense 0..n sequence after pinning may have introduced -1.
    dense = {rank: index for index, rank in enumerate(sorted(set(ranks.values())))}
    ranks = {tag: dense[rank] for tag, rank in ranks.items()}
    for tag, rank in ranks.items():
        nodes[tag].rank = rank
    rows = _order_within_ranks(ranks, edges, order)
    for tag, row in rows.items():
        nodes[tag].row = row

    width, height = _place(nodes)
    routes = _route_streams(model, nodes)

    # Back-edge routing can dip below the last row; grow the canvas to fit.
    lowest = max((y for route in routes for _, y in route.points), default=height)
    height = max(height, lowest + MARGIN)

    return Layout(nodes=nodes, routes=routes, width=width, height=height)
