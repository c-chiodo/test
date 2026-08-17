"""Render a :class:`~pid.model.PIDModel` to SVG.

Drawing conventions follow normal P&ID practice: process flow left to right,
heavy lines for process and lighter for utilities, instrument bubbles above the
equipment or line they serve on dashed signal leaders, and a title block in the
bottom-right corner.

The renderer is pure — same model in, byte-identical SVG out — so a revision
diff shows process changes rather than layout churn.
"""

from __future__ import annotations

from html import escape

from .layout import BUBBLE_BAND, BUBBLES_PER_ROW, Layout, compute_layout
from .model import PIDModel
from .symbols import BUBBLE_R, TALL_INLINE_TYPES, UPRIGHT_INLINE_TYPES, inline_symbol
from .tagging import parse_instrument_tag

MIN_WIDTH = 1180.0
#: Clear space kept between the drawing and the sheet border.
MARGIN_INSIDE = 30.0
FOOTER_GAP = 40.0
TITLE_BLOCK_W = 380.0
TITLE_BLOCK_H = 96.0
LEGEND_W = 430.0
NOTES_MIN_W = 300.0
NOTE_LINE_H = 11.0

STYLESHEET = """
  .sheet { fill: #ffffff; }
  .sym { fill: none; stroke: #14161a; stroke-width: 1.8; }
  .sym-thin { fill: none; stroke: #14161a; stroke-width: 1.1; }
  .sym-fill { fill: #14161a; stroke: #14161a; stroke-width: 1.2; }
  .sym-fill-bg { fill: #ffffff; stroke: #14161a; stroke-width: 1.8; }
  .sym-dashed { fill: none; stroke: #14161a; stroke-width: 1.6; stroke-dasharray: 5 3; }
  .process-line { fill: none; stroke: #14161a; stroke-width: 2.2; stroke-linejoin: miter; }
  .utility-line { fill: none; stroke: #14161a; stroke-width: 1.3; stroke-linejoin: miter; }
  .signal-line { fill: none; stroke: #14161a; stroke-width: 1.0; stroke-dasharray: 6 3; }
  .loop-link { fill: none; stroke: #5b6470; stroke-width: 1.0; stroke-dasharray: 2 3; }
  .frame { fill: none; stroke: #14161a; stroke-width: 1.6; }
  .rule { stroke: #14161a; stroke-width: 1.0; }
  text { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; fill: #14161a; }
  .tag { font-size: 11.5px; font-weight: 700; letter-spacing: 0.02em; }
  .name { font-size: 9.5px; }
  .line-label { font-size: 8.8px; }
  .bubble-letters { font-size: 9.5px; font-weight: 700; }
  .bubble-letters-sm { font-size: 7.6px; font-weight: 700; letter-spacing: -0.02em; }
  .bubble-number { font-size: 9px; }
  .inline-tag { font-size: 8px; }
  .title-major { font-size: 15px; font-weight: 700; }
  .title-minor { font-size: 9.5px; }
  .note { font-size: 9px; }
  .legend-head { font-size: 10px; font-weight: 700; letter-spacing: 0.06em; }
"""

ARROW_DEF = """
  <marker id="flow-arrow" viewBox="0 0 10 8" refX="9" refY="4"
          markerWidth="8" markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 4 L 0 8 z" fill="#14161a"/>
  </marker>
"""


def _text(x: float, y: float, content: str, cls: str, anchor: str = "middle") -> str:
    # quote=False: this is element content, not an attribute value, so an inch
    # mark in a line number should stay readable as 6"-PL-1201 rather than
    # becoming 6&quot;-PL-1201.
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}">'
        f"{escape(content, quote=False)}</text>"
    )


def _polyline(points: list[tuple[float, float]], cls: str, marker: bool = True) -> str:
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    end = ' marker-end="url(#flow-arrow)"' if marker else ""
    return f'<polyline points="{path}" class="{cls}"{end}/>'


def _horizontal_runs(points: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """Horizontal segments as ``(length, midpoint_x, y)``, longest first."""
    runs = []
    for i in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[i], points[i + 1]
        if abs(y2 - y1) > 0.5:
            continue
        runs.append((abs(x2 - x1), (x1 + x2) / 2, y1))
    return sorted(runs, reverse=True)


class _Occupancy:
    """Tracks what space on the sheet is already taken.

    A P&ID lives or dies on whether you can read the tags, and the fastest way
    to make one unreadable is to drop every label at its geometric ideal and let
    them pile up. Symbols, bubbles and labels are registered here as they are
    placed, so each later item can be nudged into clear space.
    """

    def __init__(self) -> None:
        self._rects: list[tuple[float, float, float, float]] = []

    def add(self, cx: float, cy: float, w: float, h: float) -> None:
        self._rects.append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))

    def add_segment(self, a: tuple[float, float], b: tuple[float, float], band: float = 4.0) -> None:
        """Reserve a thin band along a pipe run, so labels do not sit on pipes."""
        (x1, y1), (x2, y2) = a, b
        self._rects.append(
            (min(x1, x2) - band / 2, min(y1, y2) - band / 2, max(x1, x2) + band / 2, max(y1, y2) + band / 2)
        )

    def bounds(self) -> tuple[float, float, float, float]:
        """(left, top, right, bottom) of everything placed, or zeros if empty."""
        if not self._rects:
            return (0.0, 0.0, 0.0, 0.0)
        return (
            min(r[0] for r in self._rects),
            min(r[1] for r in self._rects),
            max(r[2] for r in self._rects),
            max(r[3] for r in self._rects),
        )

    def overlap(self, cx: float, cy: float, w: float, h: float) -> float:
        """Total overlapping area with everything already placed."""
        left, top, right, bottom = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        total = 0.0
        for l, t, r, b in self._rects:
            dx = min(right, r) - max(left, l)
            dy = min(bottom, b) - max(top, t)
            if dx > 0 and dy > 0:
                total += dx * dy
        return total

    def place(
        self,
        candidates: list[tuple[float, float]],
        w: float,
        h: float,
    ) -> tuple[float, float]:
        """First candidate position that is clear, else the least-crowded one."""
        best: tuple[float, tuple[float, float]] | None = None
        for cx, cy in candidates:
            crowding = self.overlap(cx, cy, w, h)
            if crowding == 0:
                self.add(cx, cy, w, h)
                return cx, cy
            if best is None or crowding < best[0]:
                best = (crowding, (cx, cy))
        chosen = best[1] if best else (0.0, 0.0)
        self.add(chosen[0], chosen[1], w, h)
        return chosen


def _text_box(content: str, font_px: float) -> tuple[float, float]:
    """Rough bounding box for a run of text. Helvetica averages ~0.52 em wide."""
    return (len(content) * font_px * 0.52 + 4, font_px + 3)


def _wrap(text: str, width: int) -> list[str]:
    """Naive word wrap for equipment names and notes."""
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


class _Renderer:
    def __init__(self, model: PIDModel, layout: Layout) -> None:
        self.model = model
        self.layout = layout
        # Where each tagged in-line component ended up, so control loops can be
        # drawn from the controller bubble to the valve it moves.
        self.inline_positions: dict[str, tuple[float, float]] = {}
        self.bubble_positions: dict[str, tuple[float, float]] = {}
        self.space = _Occupancy()

        # Seed the occupancy map with the symbols and the tag block beneath each
        # one, so nothing else is placed on top of them.
        for equipment in model.equipment:
            node = layout.node(equipment.tag)
            if node is None:
                continue
            self.space.add(node.cx, node.cy, node.width, node.height)
            label_lines = 1 if equipment.is_boundary else 1 + len(_wrap(equipment.name, 24))
            label_h = label_lines * 12
            self.space.add(node.cx, node.bottom + 8 + label_h / 2, node.width, label_h)

        # Reserve the pipe runs too. A line number belongs just above its own
        # pipe, which the 4 px band leaves room for, but it must not land on
        # somebody else's.
        for route in layout.routes:
            for index in range(len(route.points) - 1):
                self.space.add_segment(route.points[index], route.points[index + 1])

    # -- lines ------------------------------------------------------------

    def draw_line_paths(self) -> str:
        out = []
        for route in self.layout.routes:
            cls = "utility-line" if route.stream.is_utility else "process-line"
            out.append(_polyline(route.points, cls))
        return "\n".join(out)

    def draw_line_labels(self) -> str:
        """Line numbers, placed last so they can dodge everything else."""
        out = []
        for route in self.layout.routes:
            runs = _horizontal_runs(route.points)
            label = route.stream.number
            w, h = _text_box(label, 8.8)
            candidates: list[tuple[float, float]] = []
            for length, mid_x, y in runs:
                if length < w * 0.6:
                    continue
                # Prefer above the line, then below, and try shifting along the
                # run before giving up on it.
                for dx in (0.0, length * 0.28, -length * 0.28):
                    for dy in (-9.0, -21.0, 13.0, 25.0, -33.0):
                        candidates.append((mid_x + dx, y + dy))
            if not candidates:
                # Nothing horizontal enough to label; fall back to the midpoint.
                mx, my = route.midpoint()
                candidates = [(mx + 4 + w / 2, my), (mx - 4 - w / 2, my)]
            x, y = self.space.place(candidates, w, h)
            out.append(_text(x, y + 3, label, "line-label"))
        return "\n".join(out)

    def draw_inline_components(self) -> str:
        out = []
        for route in self.layout.routes:
            items = route.stream.inline
            if not items:
                continue
            for index, item in enumerate(items):
                # Keep components off the very ends of the run so they do not
                # collide with the nozzles they connect to.
                t = 0.16 + 0.68 * (index + 0.5) / len(items)
                x, y = route.point_at(t)
                angle = route.angle_at(t)
                if item.type in UPRIGHT_INLINE_TYPES:
                    rotation = 0.0
                else:
                    # Follow the pipe, but flip a right-to-left run so actuators
                    # stay above the line rather than hanging below it.
                    rotation = angle - (180.0 if 90 < abs(angle) <= 180 else 0.0)
                out.append(
                    f'<g transform="translate({x:.1f},{y:.1f}) '
                    f'rotate({rotation:.1f})">{inline_symbol(item.type)}</g>'
                )
                tall = item.type in TALL_INLINE_TYPES
                self.space.add(x, y - (10 if tall else 0), 30, 60 if tall else 40)
                if item.tag:
                    self.inline_positions[item.tag] = (x, y)
                    tw, th = _text_box(item.tag, 8.0)
                    vertical = abs(abs(angle) - 90) < 45
                    if vertical:
                        reach = 26 + tw / 2
                        candidates = [
                            (x + reach, y + 16),
                            (x - reach, y + 16),
                            (x + reach, y),
                            (x - reach, y),
                            (x + reach, y + 32),
                            (x - reach, y + 32),
                            (x, y + 38),
                        ]
                    else:
                        candidates = [
                            (x, y + 30),
                            (x, y + 42),
                            (x, y - 34),
                            (x + tw, y + 30),
                            (x - tw, y + 30),
                            (x + tw, y - 34),
                            (x - tw, y - 34),
                            (x, y + 56),
                            (x, y - 48),
                        ]
                    px, py = self.space.place(candidates, tw, th)
                    out.append(_text(px, py + 3, item.tag, "inline-tag"))
        return "\n".join(out)

    # -- equipment --------------------------------------------------------

    def draw_equipment(self) -> str:
        out = []
        for equipment in self.model.equipment:
            node = self.layout.node(equipment.tag)
            if node is None:
                continue
            out.append(
                f'<g transform="translate({node.cx:.1f},{node.cy:.1f})">{node.symbol.body}</g>'
            )
            if equipment.is_boundary:
                # The connector carries its own label inside the flag.
                lines = _wrap(equipment.name, 17)[:2]
                start = node.cy + 3 - (len(lines) - 1) * 5
                for offset, line in enumerate(lines):
                    out.append(_text(node.cx - 7, start + offset * 10, line, "name"))
                out.append(_text(node.cx, node.bottom + 14, equipment.tag, "tag"))
                continue
            out.append(_text(node.cx, node.bottom + 15, equipment.tag, "tag"))
            for offset, line in enumerate(_wrap(equipment.name, 24)):
                out.append(_text(node.cx, node.bottom + 27 + offset * 11, line, "name"))
        return "\n".join(out)

    # -- instruments ------------------------------------------------------

    def _bubble(
        self, candidates: list[tuple[float, float]], tag: str, location: str, anchor: tuple[float, float]
    ) -> str:
        """Place a bubble in the first clear candidate spot and draw its leader."""
        from .symbols import instrument_bubble

        # A diamond (PLC) needs more room than a plain circle.
        extent = BUBBLE_R * 2 * (1.35 if location == "plc" else 1.0)
        x, y = self.space.place(candidates, extent + 4, extent + 4)

        parsed = parse_instrument_tag(tag)
        letters = parsed.letters if parsed else tag
        number = (parsed.number + parsed.suffix) if parsed else ""
        letter_class = "bubble-letters" if len(letters) <= 3 else "bubble-letters-sm"

        out = (
            f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{anchor[0]:.1f}" y2="{anchor[1]:.1f}" '
            'class="signal-line"/>'
        )
        out += f'<g transform="translate({x:.1f},{y:.1f})">{instrument_bubble(location)}</g>'
        out += _text(x, y - 1, letters, letter_class)
        if number:
            out += _text(x, y + 10, number, "bubble-number")
        self.bubble_positions[tag] = (x, y)
        return out

    def draw_instruments(self) -> str:
        out = []
        spacing = BUBBLE_R * 2 + 9

        for equipment in self.model.equipment:
            node = self.layout.node(equipment.tag)
            if node is None:
                continue
            instruments = self.model.instruments_on(equipment.tag)
            for index, instrument in enumerate(instruments):
                # Preferred grid above the symbol, then progressively further up
                # and out to either side.
                candidates = []
                for band in range(index // BUBBLES_PER_ROW, index // BUBBLES_PER_ROW + 5):
                    y = node.top - BUBBLE_R - 14 - band * BUBBLE_BAND
                    for column in (index % BUBBLES_PER_ROW, 0, 1, 2, -1, 3):
                        x = node.cx + (column - 1) * spacing
                        candidates.append((x, y))
                out.append(
                    self._bubble(
                        candidates, instrument.tag, instrument.location, (node.cx, node.top)
                    )
                )

        for route in self.layout.routes:
            instruments = self.model.instruments_on(route.stream.number)
            for index, instrument in enumerate(instruments):
                anchor_t = 0.2 + 0.6 * (index + 0.5) / len(instruments)
                ax, ay = route.point_at(anchor_t)
                candidates = [
                    (ax, ay - BUBBLE_R - 34 - step * BUBBLE_BAND) for step in range(5)
                ]
                candidates += [
                    (ax + dx, ay - BUBBLE_R - 34 - step * BUBBLE_BAND)
                    for step in range(4)
                    for dx in (spacing, -spacing)
                ]
                candidates += [(ax, ay + BUBBLE_R + 34 + step * BUBBLE_BAND) for step in range(3)]
                out.append(
                    self._bubble(candidates, instrument.tag, instrument.location, (ax, ay))
                )
        return "\n".join(out)

    def draw_loop_links(self) -> str:
        """Dashed link from each controller to the element it moves."""
        out = []
        for loop in self.model.loops:
            controller = self.bubble_positions.get(loop.controller_tag)
            target = self.inline_positions.get(loop.final_element_tag) or self.bubble_positions.get(
                loop.final_element_tag
            )
            if controller is None or target is None:
                continue
            (cx, cy), (tx, ty) = controller, target
            # Route via a horizontal then vertical leg so the link reads as a
            # signal run rather than a pipe.
            out.append(
                f'<polyline points="{cx:.1f},{cy + BUBBLE_R:.1f} {cx:.1f},{(cy + ty) / 2:.1f} '
                f'{tx:.1f},{(cy + ty) / 2:.1f} {tx:.1f},{ty - 20:.1f}" class="loop-link"/>'
            )
        return "\n".join(out)

    # -- sheet furniture --------------------------------------------------

    def draw_title_block(self, sheet_w: float, footer_bottom: float) -> str:
        x = sheet_w - TITLE_BLOCK_W - 24
        y = footer_bottom - TITLE_BLOCK_H
        out = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{TITLE_BLOCK_W}" height="{TITLE_BLOCK_H}" class="frame"/>']
        out.append(f'<line x1="{x:.1f}" y1="{y + 30:.1f}" x2="{x + TITLE_BLOCK_W:.1f}" y2="{y + 30:.1f}" class="rule"/>')
        out.append(f'<line x1="{x:.1f}" y1="{y + 62:.1f}" x2="{x + TITLE_BLOCK_W:.1f}" y2="{y + 62:.1f}" class="rule"/>')
        out.append(f'<line x1="{x + TITLE_BLOCK_W - 76:.1f}" y1="{y + 62:.1f}" x2="{x + TITLE_BLOCK_W - 76:.1f}" y2="{y + TITLE_BLOCK_H:.1f}" class="rule"/>')

        out.append(_text(x + 10, y + 20, self.model.project or "—", "title-minor", "start"))
        out.append(_text(x + 10, y + 52, self.model.title, "title-major", "start"))
        out.append(
            _text(
                x + 10,
                y + 80,
                f"DWG {self.model.drawing_number or '—'}",
                "title-minor",
                "start",
            )
        )
        out.append(_text(x + TITLE_BLOCK_W - 38, y + 74, "REV", "title-minor"))
        out.append(_text(x + TITLE_BLOCK_W - 38, y + 88, self.model.revision or "—", "title-major"))
        return "\n".join(out)

    def draw_legend(self, footer_bottom: float) -> str:
        """Key for the line and bubble conventions actually used on this sheet."""
        from .symbols import instrument_bubble

        x = 24.0
        y = footer_bottom - TITLE_BLOCK_H
        out = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{LEGEND_W}" height="{TITLE_BLOCK_H}" class="frame"/>']
        out.append(_text(x + 10, y + 16, "LEGEND", "legend-head", "start"))

        entries: list[tuple[str, str]] = [("process-line", "Process line"), ("utility-line", "Utility line"), ("signal-line", "Instrument signal")]
        for index, (cls, label) in enumerate(entries):
            ly = y + 34 + index * 18
            out.append(f'<line x1="{x + 12:.1f}" y1="{ly:.1f}" x2="{x + 62:.1f}" y2="{ly:.1f}" class="{cls}"/>')
            out.append(_text(x + 70, ly + 3, label, "note", "start"))

        used_locations: list[str] = []
        for instrument in self.model.instruments:
            if instrument.location not in used_locations:
                used_locations.append(instrument.location)
        labels = {
            "field": "Field",
            "field_aux": "Field aux.",
            "shared_display": "DCS",
            "shared_aux": "DCS aux.",
            "computer": "Computer",
            "plc": "PLC",
            "inaccessible": "Inaccessible",
        }
        for index, location in enumerate(used_locations[:4]):
            bx = x + 220 + (index % 2) * 100
            by = y + 40 + (index // 2) * 40
            out.append(f'<g transform="translate({bx:.1f},{by:.1f}) scale(0.62)">{instrument_bubble(location)}</g>')
            out.append(_text(bx + 16, by + 4, labels.get(location, location), "note", "start"))
        return "\n".join(out)

    def note_lines(self, wrap_at: int) -> list[str]:
        """Notes and assumptions as numbered, wrapped lines."""
        notes = list(self.model.notes)
        notes += [f"Assumed: {assumption}" for assumption in self.model.assumptions]
        lines: list[str] = []
        for index, note in enumerate(notes, start=1):
            for offset, chunk in enumerate(_wrap(note, wrap_at)):
                lines.append((f"{index}. " if offset == 0 else "   ") + chunk)
        return lines

    def draw_notes(self, x: float, footer_top: float, wrap_at: int) -> str:
        """Notes, in the footer band between the legend and the title block."""
        lines = self.note_lines(wrap_at)
        if not lines:
            return ""
        out = [_text(x, footer_top + 12, "NOTES", "legend-head", "start")]
        for index, line in enumerate(lines):
            out.append(_text(x, footer_top + 26 + index * NOTE_LINE_H, line, "note", "start"))
        return "\n".join(out)


def render_svg(model: PIDModel, layout: Layout | None = None) -> str:
    """Render ``model`` as a standalone SVG document."""
    layout = layout or compute_layout(model)
    renderer = _Renderer(model, layout)

    # Placement order is not drawing order. Symbols are registered first, then
    # in-line components, then bubbles, then line numbers — so each later item
    # can dodge everything already placed. Drawing then goes back to visual
    # order, with line numbers on top where they stay readable.
    lines = renderer.draw_line_paths()
    inline = renderer.draw_inline_components()
    equipment = renderer.draw_equipment()
    instruments = renderer.draw_instruments()
    loop_links = renderer.draw_loop_links()
    line_labels = renderer.draw_line_labels()

    # Collision avoidance can push a bubble above or below where the layout
    # reserved room for it, so size the sheet to what was actually placed and
    # shift the whole drawing clear of the border.
    left, top, right, bottom = renderer.space.bounds()
    shift_x = max(0.0, MARGIN_INSIDE - left)
    shift_y = max(0.0, MARGIN_INSIDE - top)
    drawing_h = max(layout.height, bottom + shift_y + MARGIN_INSIDE)
    drawing_w = max(layout.width, right + shift_x + MARGIN_INSIDE)

    sheet_w = max(MIN_WIDTH, drawing_w, LEGEND_W + TITLE_BLOCK_W + NOTES_MIN_W + 96)
    # The footer holds the legend, the notes and the title block side by side, so
    # it grows with the note count instead of the notes creeping up into the
    # drawing.
    notes_x = 24 + LEGEND_W + 32
    notes_w = sheet_w - notes_x - TITLE_BLOCK_W - 48
    notes_wrap = max(28, int(notes_w / 4.9))
    footer_top = drawing_h + FOOTER_GAP
    footer_h = max(
        TITLE_BLOCK_H, len(renderer.note_lines(notes_wrap)) * NOTE_LINE_H + 34
    )
    footer_bottom = footer_top + footer_h
    sheet_h = footer_bottom + 24

    body = "\n".join(
        part
        for part in (lines, inline, equipment, instruments, loop_links, line_labels)
        if part
    )
    drawing = (
        f'<g transform="translate({shift_x:.1f},{shift_y:.1f})">\n{body}\n</g>'
        if (shift_x or shift_y)
        else body
    )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{sheet_w:.0f}" height="{sheet_h:.0f}" '
        f'viewBox="0 0 {sheet_w:.0f} {sheet_h:.0f}" role="img" '
        f'aria-label="{escape(model.title)} piping and instrumentation diagram">',
        f"<defs><style>{STYLESHEET}</style>{ARROW_DEF}</defs>",
        f'<rect width="{sheet_w:.0f}" height="{sheet_h:.0f}" class="sheet"/>',
        f'<rect x="10" y="10" width="{sheet_w - 20:.0f}" height="{sheet_h - 20:.0f}" class="frame"/>',
        drawing,
        renderer.draw_legend(footer_bottom),
        renderer.draw_notes(notes_x, footer_top, notes_wrap),
        renderer.draw_title_block(sheet_w, footer_bottom),
        "</svg>",
    ]
    return "\n".join(part for part in parts if part)
