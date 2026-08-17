"""ISA-5.1 style symbol geometry, as SVG fragments centred on the origin.

Each symbol function returns a :class:`Symbol`: the SVG body plus the bounding
box the layout engine needs to reserve. Bodies are drawn around ``(0, 0)`` so
the renderer can place them with a single ``translate``.

Strokes use CSS classes (``sym``, ``sym-thin``, ``sym-fill``) defined once in
the renderer's stylesheet, so line weights stay consistent and the whole sheet
can be restyled from one place.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Symbol:
    body: str
    width: float
    height: float
    #: Where lines should attach, as fractions of the bounding box. Overridden
    #: by symbols whose natural connection points are not the box edges.
    inlet_side: str = "left"
    outlet_side: str = "right"


def _rect(w: float, h: float, cls: str = "sym", rx: float = 0) -> str:
    return (
        f'<rect x="{-w / 2:.1f}" y="{-h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="{rx:.1f}" class="{cls}"/>'
    )


def _line(x1: float, y1: float, x2: float, y2: float, cls: str = "sym-thin") -> str:
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" class="{cls}"/>'


def _circle(r: float, cls: str = "sym", cx: float = 0, cy: float = 0) -> str:
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" class="{cls}"/>'


# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------


def pump() -> Symbol:
    """Centrifugal pump: circle on a baseline with a discharge volute."""
    r = 22.0
    body = (
        _circle(r)
        # Volute / discharge nozzle pointing up-right, the ISA convention.
        + f'<path d="M {r * 0.7:.1f} {-r * 0.7:.1f} L {r + 12:.1f} {-r - 6:.1f} '
        f'L {r + 12:.1f} {-r + 6:.1f} Z" class="sym-fill"/>'
        + _line(-r - 4, r + 6, r + 4, r + 6, "sym")
    )
    return Symbol(body, r * 2 + 24, r * 2 + 20)


def tank() -> Symbol:
    """Atmospheric storage tank: flat top, slightly dished bottom."""
    w, h = 118.0, 92.0
    body = (
        f'<path d="M {-w / 2:.1f} {-h / 2:.1f} L {w / 2:.1f} {-h / 2:.1f} '
        f"L {w / 2:.1f} {h / 2 - 10:.1f} "
        f"Q 0 {h / 2 + 8:.1f} {-w / 2:.1f} {h / 2 - 10:.1f} Z\" class=\"sym\"/>"
    )
    return Symbol(body, w, h)


def vessel() -> Symbol:
    """Vertical pressure vessel: shell with 2:1 elliptical heads."""
    w, h = 84.0, 132.0
    shell = h - 32
    body = (
        f'<path d="M {-w / 2:.1f} {-shell / 2:.1f} '
        f"A {w / 2:.1f} 16 0 0 1 {w / 2:.1f} {-shell / 2:.1f} "
        f"L {w / 2:.1f} {shell / 2:.1f} "
        f"A {w / 2:.1f} 16 0 0 1 {-w / 2:.1f} {shell / 2:.1f} Z\" class=\"sym\"/>"
    )
    return Symbol(body, w, h)


def separator() -> Symbol:
    """Horizontal separator: shell with heads on the ends."""
    w, h = 140.0, 74.0
    shell = w - 32
    body = (
        f'<path d="M {-shell / 2:.1f} {-h / 2:.1f} L {shell / 2:.1f} {-h / 2:.1f} '
        f"A 16 {h / 2:.1f} 0 0 1 {shell / 2:.1f} {h / 2:.1f} "
        f"L {-shell / 2:.1f} {h / 2:.1f} "
        f"A 16 {h / 2:.1f} 0 0 1 {-shell / 2:.1f} {-h / 2:.1f} Z\" class=\"sym\"/>"
        + _line(-shell / 2, 8, shell / 2, 8)  # interface level
    )
    return Symbol(body, w, h)


def reactor() -> Symbol:
    """Agitated reactor: vessel plus agitator shaft, turbine and drive."""
    base = vessel()
    hub = base.height * 0.18
    motor_y = -base.height / 2 - 20
    body = (
        base.body
        + _line(0, motor_y, 0, hub, "sym")  # shaft
        + _line(-16, hub, 16, hub, "sym")  # turbine
        + _line(-10, hub - 8, -10, hub + 8, "sym")
        + _line(10, hub - 8, 10, hub + 8, "sym")
        + f'<rect x="-12" y="{motor_y:.1f}" width="24" height="14" rx="2" class="sym-fill"/>'
    )
    return Symbol(body, base.width, base.height + 20)


def mixer() -> Symbol:
    """In-line or vessel mixer — drawn as an agitated vessel."""
    return reactor()


def column() -> Symbol:
    """Packed or trayed column: tall shell with internals."""
    w, h = 66.0, 190.0
    shell = h - 26
    body = (
        f'<path d="M {-w / 2:.1f} {-shell / 2:.1f} '
        f"A {w / 2:.1f} 13 0 0 1 {w / 2:.1f} {-shell / 2:.1f} "
        f"L {w / 2:.1f} {shell / 2:.1f} "
        f"A {w / 2:.1f} 13 0 0 1 {-w / 2:.1f} {shell / 2:.1f} Z\" class=\"sym\"/>"
    )
    for i in range(-2, 3):
        y = i * 28.0
        body += _line(-w / 2, y, w / 2, y)
    return Symbol(body, w, h)


def heat_exchanger() -> Symbol:
    """Shell-and-tube exchanger: shell with a U-tube bundle."""
    w, h = 124.0, 72.0
    body = _rect(w, h, "sym", 4)
    # U-tube bundle.
    body += (
        f'<path d="M {-w / 2:.1f} -16 L {w / 2 - 18:.1f} -16 '
        f"A 16 16 0 0 1 {w / 2 - 18:.1f} 16 L {-w / 2:.1f} 16\" class=\"sym-thin\" "
        'fill="none"/>'
    )
    # Tubesheet.
    body += _line(-w / 2 + 14, -h / 2, -w / 2 + 14, h / 2)
    return Symbol(body, w, h)


def filter_() -> Symbol:
    """Filter / bleacher press: vessel with a filtration element."""
    w, h = 86.0, 104.0
    body = _rect(w, h, "sym", 4)
    for i in range(-2, 3):
        x = i * 16.0
        body += _line(x, -h / 2 + 10, x, h / 2 - 10)
    body += _line(-w / 2, -h / 2 + 10, w / 2, -h / 2 + 10)
    body += _line(-w / 2, h / 2 - 10, w / 2, h / 2 - 10)
    return Symbol(body, w, h)


def compressor() -> Symbol:
    """Centrifugal compressor: circle with a wedge showing gas path."""
    r = 26.0
    body = _circle(r) + (
        f'<path d="M {-r:.1f} {-r * 0.55:.1f} L {r:.1f} {-r * 0.28:.1f} '
        f'L {r:.1f} {r * 0.28:.1f} L {-r:.1f} {r * 0.55:.1f} Z" class="sym-thin"/>'
    )
    return Symbol(body, r * 2, r * 2)


def blower() -> Symbol:
    """Blower / fan: circle with an impeller."""
    r = 24.0
    body = (
        _circle(r)
        + f'<path d="M 0 0 L {r:.1f} {-r * 0.5:.1f} A {r:.1f} {r:.1f} 0 0 0 {r * 0.5:.1f} '
        f'{r:.1f} Z" class="sym-thin"/>'
    )
    return Symbol(body, r * 2, r * 2)


def centrifuge() -> Symbol:
    """Disc-stack centrifuge: bowl inside a casing."""
    w, h = 96.0, 86.0
    body = _rect(w, h, "sym", 6) + _circle(24, "sym-thin") + _circle(10, "sym-thin")
    return Symbol(body, w, h)


def dryer() -> Symbol:
    """Dryer: vessel with a drying-medium path."""
    w, h = 118.0, 80.0
    body = _rect(w, h, "sym", 4)
    body += (
        f'<path d="M {-w / 2 + 8:.1f} 0 q 14 -18 28 0 q 14 18 28 0 q 14 -18 28 0" '
        'class="sym-thin" fill="none"/>'
    )
    return Symbol(body, w, h)


def conveyor() -> Symbol:
    """Belt or screw conveyor."""
    w, h = 130.0, 44.0
    body = _rect(w, h, "sym", h / 2)
    body += _circle(12, "sym-thin", cx=-w / 2 + 16) + _circle(12, "sym-thin", cx=w / 2 - 16)
    return Symbol(body, w, h)


def scale() -> Symbol:
    """Weigh hopper / scale."""
    w, h = 96.0, 78.0
    body = (
        f'<path d="M {-w / 2:.1f} {-h / 2:.1f} L {w / 2:.1f} {-h / 2:.1f} '
        f'L {w / 2 - 22:.1f} {h / 2:.1f} L {-w / 2 + 22:.1f} {h / 2:.1f} Z" class="sym"/>'
    )
    body += _line(-w / 2 + 18, h / 2, -w / 2 + 10, h / 2 + 10, "sym")
    body += _line(w / 2 - 18, h / 2, w / 2 - 10, h / 2 + 10, "sym")
    return Symbol(body, w, h + 12)


def boundary() -> Symbol:
    """Off-page / off-site connector: the flag arrow used on P&IDs."""
    w, h = 128.0, 38.0
    body = (
        f'<path d="M {-w / 2:.1f} {-h / 2:.1f} L {w / 2 - 14:.1f} {-h / 2:.1f} '
        f"L {w / 2:.1f} 0 L {w / 2 - 14:.1f} {h / 2:.1f} L {-w / 2:.1f} {h / 2:.1f} Z\" "
        'class="sym"/>'
    )
    return Symbol(body, w, h)


def unknown() -> Symbol:
    """Fallback for an equipment class with no symbol: a plain box."""
    w, h = 104.0, 72.0
    return Symbol(_rect(w, h, "sym", 2) + _line(-w / 2, -h / 2, w / 2, h / 2), w, h)


EQUIPMENT_SYMBOLS = {
    "pump": pump,
    "tank": tank,
    "vessel": vessel,
    "reactor": reactor,
    "mixer": mixer,
    "column": column,
    "heat_exchanger": heat_exchanger,
    "filter": filter_,
    "compressor": compressor,
    "blower": blower,
    "centrifuge": centrifuge,
    "dryer": dryer,
    "conveyor": conveyor,
    "scale": scale,
    "separator": separator,
    "boundary": boundary,
}


def equipment_symbol(kind: str) -> Symbol:
    return EQUIPMENT_SYMBOLS.get(kind, unknown)()


# ---------------------------------------------------------------------------
# In-line components — drawn centred on a point, rotated to the line direction
# ---------------------------------------------------------------------------

VALVE_HALF = 9.0

#: Components conventionally drawn upright whatever direction the pipe runs. A
#: relief valve's angle body and spring bonnet is a recognised glyph; rotating it
#: to follow a vertical pipe turns it into something a reviewer has to decode.
UPRIGHT_INLINE_TYPES = frozenset(
    {"relief_valve", "rupture_disc", "steam_trap", "sight_glass", "sample_point"}
)

#: Components whose symbol extends well above the pipe centreline, so the
#: renderer knows to reserve more room for them.
TALL_INLINE_TYPES = frozenset({"control_valve", "relief_valve", "three_way_valve"})


def _bowtie(cls: str = "sym") -> str:
    v = VALVE_HALF
    return (
        f'<path d="M {-v:.1f} {-v:.1f} L {-v:.1f} {v:.1f} L {v:.1f} {-v:.1f} '
        f'L {v:.1f} {v:.1f} Z" class="{cls}"/>'
    )


def _actuator(shape: str) -> str:
    """Actuator drawn above a valve body. ``shape`` is 'diaphragm' or 'motor'."""
    v = VALVE_HALF
    stem = _line(0, -v, 0, -v - 10, "sym")
    if shape == "diaphragm":
        return stem + (
            f'<path d="M -11 {-v - 10:.1f} A 11 8 0 0 1 11 {-v - 10:.1f} Z" class="sym"/>'
        )
    return stem + f'<rect x="-9" y="{-v - 20:.1f}" width="18" height="10" class="sym"/>'


def inline_symbol(kind: str) -> str:
    """SVG for an in-line component, centred on the origin, drawn for a
    horizontal line. The renderer rotates it to match the line direction."""
    v = VALVE_HALF
    if kind == "control_valve":
        return _bowtie() + _actuator("diaphragm")
    if kind in {"gate_valve", "ball_valve", "globe_valve", "butterfly_valve"}:
        body = _bowtie()
        if kind == "ball_valve":
            body += _circle(4.5, "sym-thin")
        elif kind == "globe_valve":
            body += _circle(4.5, "sym-fill")
        elif kind == "butterfly_valve":
            body += _line(-4, -v + 2, 4, v - 2, "sym")
        body += _line(0, -v, 0, -v - 7, "sym") + _line(-6, -v - 7, 6, -v - 7, "sym")
        return body
    if kind == "check_valve":
        return (
            _line(-v, -v, -v, v, "sym")
            + f'<path d="M {-v:.1f} {-v:.1f} L {v:.1f} 0 L {-v:.1f} {v:.1f} Z" class="sym-fill"/>'
        )
    if kind == "three_way_valve":
        return _bowtie() + (
            f'<path d="M 0 0 L {-v:.1f} {v + 6:.1f} L {v:.1f} {v + 6:.1f} Z" class="sym"/>'
        )
    if kind in {"relief_valve", "rupture_disc"}:
        if kind == "rupture_disc":
            return (
                _line(0, -v - 2, 0, v + 2, "sym")
                + f'<path d="M -7 {-v - 2:.1f} Q 0 0 -7 {v + 2:.1f}" class="sym-thin" fill="none"/>'
            )
        # Relief valve: angle body with a spring bonnet.
        return (
            f'<path d="M {-v:.1f} {-v:.1f} L {-v:.1f} {v:.1f} L {v:.1f} 0 Z" class="sym"/>'
            + _line(0, -v, 0, -v - 12, "sym")
            + f'<path d="M -7 {-v - 12:.1f} l 14 -4 l -14 -4 l 14 -4" class="sym-thin" fill="none"/>'
        )
    if kind == "orifice":
        return _line(0, -v - 3, 0, -3, "sym") + _line(0, 3, 0, v + 3, "sym")
    if kind == "strainer":
        return (
            _bowtie("sym-thin")
            + f'<path d="M {-v:.1f} {v:.1f} L 0 {v + 9:.1f} L {v:.1f} {v:.1f}" class="sym-thin" fill="none"/>'
        )
    if kind == "sight_glass":
        return _circle(7, "sym") + _line(-7, 0, 7, 0, "sym-thin")
    if kind == "reducer":
        return (
            f'<path d="M {-v:.1f} {-v:.1f} L {v:.1f} {-v * 0.5:.1f} '
            f'L {v:.1f} {v * 0.5:.1f} L {-v:.1f} {v:.1f} Z" class="sym"/>'
        )
    if kind == "spectacle_blind":
        return _circle(5, "sym-fill", cx=-6) + _circle(5, "sym", cx=6) + _line(-6, 0, 6, 0, "sym-thin")
    if kind == "sample_point":
        return _line(0, 0, 0, v + 10, "sym") + _bowtie("sym-thin")
    if kind == "steam_trap":
        return _circle(8, "sym") + _circle(3.5, "sym-fill")
    if kind == "flame_arrestor":
        body = _rect(16, 2 * v, "sym")
        for x in (-4, 0, 4):
            body += _line(x, -v, x, v)
        return body
    # Unknown component: a small hollow square so it is visible and obviously odd.
    return _rect(12, 12, "sym-thin", 1)


# ---------------------------------------------------------------------------
# Instrument bubbles (ISA-5.1 Table 4)
# ---------------------------------------------------------------------------

BUBBLE_R = 16.5


def instrument_bubble(location: str) -> str:
    """The bubble outline for an instrument's readout location.

    * field mounted — plain circle
    * shared display / control (DCS) — circle inside a square
    * computer function — hexagon
    * PLC — circle inside a diamond
    * auxiliary location — circle with a double horizontal bar
    * inaccessible — dashed circle
    """
    r = BUBBLE_R
    if location == "shared_display":
        return _rect(r * 2, r * 2, "sym-fill-bg") + _circle(r) + _line(-r, 0, r, 0, "sym-thin")
    if location == "shared_aux":
        return (
            _rect(r * 2, r * 2, "sym-fill-bg")
            + _circle(r)
            + _line(-r, -3, r, -3, "sym-thin")
            + _line(-r, 3, r, 3, "sym-thin")
        )
    if location == "computer":
        pts = " ".join(
            f"{r * c:.1f},{r * s:.1f}"
            for c, s in ((-1, -0.5), (0, -1), (1, -0.5), (1, 0.5), (0, 1), (-1, 0.5))
        )
        return f'<polygon points="{pts}" class="sym-fill-bg"/>' + _line(-r, 0, r, 0, "sym-thin")
    if location == "plc":
        pts = f"0,{-r * 1.35:.1f} {r * 1.35:.1f},0 0,{r * 1.35:.1f} {-r * 1.35:.1f},0"
        return f'<polygon points="{pts}" class="sym-fill-bg"/>' + _circle(r * 0.8)
    if location == "field_aux":
        return _circle(r) + _line(-r, -3, r, -3, "sym-thin") + _line(-r, 3, r, 3, "sym-thin")
    if location == "inaccessible":
        return _circle(r, "sym-dashed")
    return _circle(r)  # field
