"""Parsing, validation and generation of P&ID tags.

Three tag grammars live here:

* **Instrument tags** — ``[AREA-]LETTERS-NUMBER[SUFFIX]``, e.g. ``FIC-1201``,
  ``TE-1201A``, ``LSHH-1101``, ``12-PDT-304``.
* **Equipment tags** — ``PREFIX-NUMBER[SUFFIX]``, e.g. ``P-1201A``, ``TK-1101``.
* **Line numbers** — ``SIZE"-SERVICE-SEQUENCE-SPEC``, e.g. ``6"-PL-1201-CS150``
  or ``1-1/2"-ST-1310-CS300``.

Everything is pure parsing over the tables in :mod:`pid.standards`, so it runs
without an API key and is what the rule engine leans on to check the agents'
work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

from .standards import (
    EQUIPMENT_PREFIXES,
    FINAL_ELEMENT_LETTERS,
    MEASURED_VARIABLES,
    NOMINAL_SIZES,
    PIPE_SPECS,
    SENSING_LETTERS,
    SERVICE_CODES,
    SUCCEEDING_LETTERS,
)

_INSTRUMENT_RE = re.compile(r"^(?:(?P<area>\d{1,3})-)?(?P<letters>[A-Z]{1,6})-(?P<number>\d{1,5})(?P<suffix>[A-Z]{0,2})$")
_EQUIPMENT_RE = re.compile(r"^(?P<prefix>[A-Z]{1,4})-(?P<number>\d{1,5})(?P<suffix>[A-Z]{0,2})$")
_LINE_RE = re.compile(
    r'^(?P<size>\d+(?:-\d+/\d+)?(?:\.\d+)?)"'
    r"-(?P<service>[A-Z0-9]{1,4})"
    r"-(?P<sequence>\d{1,5})"
    r"(?:-(?P<spec>[A-Z0-9]{2,8}))?$"
)

# Letters that qualify the function immediately before them and must therefore
# sit at the tail of the letter string: PSH, LSLL, TAHH.
_TRAILING_MODIFIERS = frozenset("HLM")


def _leading_term(meaning: str) -> str:
    """'Switch / safety (modifier)' -> 'Switch'."""
    return meaning.split(" / ")[0].replace(" (modifier)", "").strip()


@dataclass
class InstrumentTag:
    """A parsed instrument tag."""

    raw: str
    letters: str
    number: str
    suffix: str = ""
    area: str = ""

    @property
    def variable(self) -> str:
        """First letter — the measured variable."""
        return self.letters[0]

    @property
    def functions(self) -> str:
        """Succeeding letters — modifiers and functions."""
        return self.letters[1:]

    @property
    def loop(self) -> str:
        """Loop number. Instruments sharing a loop share this."""
        return self.number

    @property
    def is_controller(self) -> bool:
        return "C" in self.functions

    @property
    def is_transmitter(self) -> bool:
        return "T" in self.functions

    @property
    def is_sensor(self) -> bool:
        return bool(SENSING_LETTERS & set(self.functions))

    @property
    def is_final_element(self) -> bool:
        return bool(FINAL_ELEMENT_LETTERS & set(self.functions))

    @property
    def is_switch(self) -> bool:
        return "S" in self.functions

    @property
    def is_alarm(self) -> bool:
        return "A" in self.functions

    @property
    def is_relief_device(self) -> bool:
        # PSV / PSE / PRV are relief devices rather than measurement loops.
        return self.letters in {"PSV", "PSE", "PRV", "TSV", "RD"}

    def describe(self) -> str:
        """Expand the tag for the instrument index.

        ``FIC`` -> ``Flow rate / Indicate / Control``, ``LSLL`` -> ``Level /
        Switch / Low / Low``. The tables in :mod:`pid.standards` carry the full
        ISA wording including synonyms and "(modifier)" annotations; those are
        right for a reference but too noisy for a column in a schedule, so only
        the leading term of each is used here.
        """
        letters = [
            MEASURED_VARIABLES.get(self.variable, f"Unknown variable '{self.variable}'")
        ]
        letters += [
            SUCCEEDING_LETTERS.get(letter, f"Unknown function '{letter}'")
            for letter in self.functions
        ]
        return " / ".join(_leading_term(meaning) for meaning in letters)


@dataclass
class EquipmentTag:
    raw: str
    prefix: str
    number: str
    suffix: str = ""

    @property
    def kind(self) -> str | None:
        """Equipment class implied by the prefix, if the prefix is a known one."""
        for kind, prefix in EQUIPMENT_PREFIXES.items():
            if prefix == self.prefix:
                return kind
        return None


@dataclass
class LineNumber:
    raw: str
    size_in: float
    service: str
    sequence: str
    spec: str = ""


@dataclass
class TagIssues:
    """Result of validating a tag: a list of problems, empty if the tag is good."""

    tag: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# Instrument tags
# ---------------------------------------------------------------------------


def parse_instrument_tag(tag: str) -> InstrumentTag | None:
    """Parse an ISA-5.1 instrument tag, or return None if it does not match."""
    match = _INSTRUMENT_RE.match(tag.strip())
    if not match:
        return None
    return InstrumentTag(
        raw=tag.strip(),
        letters=match["letters"],
        number=match["number"],
        suffix=match["suffix"] or "",
        area=match["area"] or "",
    )


def validate_instrument_tag(tag: str) -> TagIssues:
    """Check an instrument tag against ISA-5.1 letter conventions."""
    issues = TagIssues(tag=tag)
    parsed = parse_instrument_tag(tag)
    if parsed is None:
        issues.errors.append(
            f"{tag!r} is not a valid instrument tag; expected LETTERS-NUMBER "
            "with optional area prefix and letter suffix, e.g. 'FIC-1201' or 'TE-1201A'"
        )
        return issues

    if parsed.variable not in MEASURED_VARIABLES:
        issues.errors.append(
            f"{tag!r}: '{parsed.variable}' is not an ISA-5.1 measured variable (first) letter"
        )

    if not parsed.functions:
        issues.errors.append(
            f"{tag!r}: needs at least one succeeding letter to say what the instrument does "
            "(e.g. 'T' transmit, 'I' indicate, 'C' control)"
        )
        return issues

    for letter in parsed.functions:
        if letter not in SUCCEEDING_LETTERS:
            issues.errors.append(f"{tag!r}: '{letter}' is not an ISA-5.1 succeeding letter")

    # D and F modify the measured variable, so they belong immediately after it.
    for letter in "DF":
        position = parsed.functions.find(letter)
        if position > 0:
            issues.warnings.append(
                f"{tag!r}: variable modifier '{letter}' should immediately follow the "
                f"first letter (e.g. 'PDT' not '{tag.split('-')[0]}')"
            )

    # H / L / M qualify the function before them and belong at the tail.
    trailing = parsed.functions
    while trailing and trailing[-1] in _TRAILING_MODIFIERS:
        trailing = trailing[:-1]
    if set(trailing) & _TRAILING_MODIFIERS:
        issues.warnings.append(
            f"{tag!r}: high/low/middle modifiers should come last, e.g. 'PSH' or 'LSLL'"
        )

    if not trailing:
        issues.errors.append(
            f"{tag!r}: the letters are all modifiers with no function letter — "
            "a tag like 'PH-1201' does not say what the device does"
        )

    if parsed.is_controller and parsed.is_sensor:
        issues.warnings.append(
            f"{tag!r}: combines a controller ('C') with a primary element "
            f"('{''.join(sorted(SENSING_LETTERS & set(parsed.functions)))}') — these are "
            "normally separate tags sharing one loop number"
        )

    return issues


def describe_instrument_tag(tag: str) -> str:
    """Human-readable expansion of a tag, for the instrument index."""
    parsed = parse_instrument_tag(tag)
    return parsed.describe() if parsed else f"Unrecognised tag {tag!r}"


def loop_number(tag: str) -> str | None:
    """The loop number a tag belongs to, e.g. 'TV-1201' -> '1201'."""
    parsed = parse_instrument_tag(tag)
    return parsed.number if parsed else None


# ---------------------------------------------------------------------------
# Equipment tags
# ---------------------------------------------------------------------------


def parse_equipment_tag(tag: str) -> EquipmentTag | None:
    match = _EQUIPMENT_RE.match(tag.strip())
    if not match:
        return None
    return EquipmentTag(
        raw=tag.strip(),
        prefix=match["prefix"],
        number=match["number"],
        suffix=match["suffix"] or "",
    )


def validate_equipment_tag(tag: str, kind: str | None = None) -> TagIssues:
    """Check an equipment tag's shape, and that its prefix matches its class."""
    issues = TagIssues(tag=tag)
    parsed = parse_equipment_tag(tag)
    if parsed is None:
        issues.errors.append(
            f"{tag!r} is not a valid equipment tag; expected PREFIX-NUMBER, e.g. 'P-1201A'"
        )
        return issues

    if kind is not None:
        expected = EQUIPMENT_PREFIXES.get(kind)
        if expected is None:
            issues.warnings.append(f"{tag!r}: '{kind}' is not a known equipment class")
        elif parsed.prefix != expected:
            issues.warnings.append(
                f"{tag!r}: a {kind} is conventionally tagged '{expected}-...', not '{parsed.prefix}-...'"
            )
    return issues


def next_equipment_tag(kind: str, existing: list[str], area: int = 1000) -> str:
    """Suggest the next free tag for an equipment class.

    ``next_equipment_tag('pump', ['P-1201', 'P-1202'], area=1200)`` -> ``'P-1203'``.
    """
    prefix = EQUIPMENT_PREFIXES.get(kind, "X")
    used: set[int] = set()
    for tag in existing:
        parsed = parse_equipment_tag(tag)
        if parsed and parsed.prefix == prefix:
            used.add(int(parsed.number))
    candidate = max(used) + 1 if used else area + 1
    while candidate in used:
        candidate += 1
    return f"{prefix}-{candidate}"


# ---------------------------------------------------------------------------
# Line numbers
# ---------------------------------------------------------------------------


def _parse_size(raw: str) -> float:
    """Turn '6', '1-1/2' or '0.75' into a float."""
    if "-" in raw:
        whole, frac = raw.split("-", 1)
        return float(whole) + float(Fraction(frac))
    return float(raw)


def format_size(size_in: float) -> str:
    """Render a nominal size the way it appears on a drawing: 1.5 -> '1-1/2'."""
    whole = int(size_in)
    remainder = Fraction(size_in - whole).limit_denominator(8)
    if remainder == 0:
        return str(whole)
    if whole == 0:
        return str(remainder)
    return f"{whole}-{remainder}"


def parse_line_number(number: str) -> LineNumber | None:
    match = _LINE_RE.match(number.strip())
    if not match:
        return None
    return LineNumber(
        raw=number.strip(),
        size_in=_parse_size(match["size"]),
        service=match["service"],
        sequence=match["sequence"],
        spec=match["spec"] or "",
    )


def format_line_number(size_in: float, service: str, sequence: str | int, spec: str = "") -> str:
    """Build a line number from its parts."""
    base = f'{format_size(size_in)}"-{service}-{sequence}'
    return f"{base}-{spec}" if spec else base


def validate_line_number(number: str) -> TagIssues:
    """Check a line number's shape, service code, size and piping spec."""
    issues = TagIssues(tag=number)
    parsed = parse_line_number(number)
    if parsed is None:
        issues.errors.append(
            f"{number!r} is not a valid line number; expected SIZE\"-SERVICE-SEQUENCE-SPEC, "
            "e.g. '6\"-PL-1201-CS150'"
        )
        return issues

    if parsed.service not in SERVICE_CODES:
        issues.warnings.append(
            f"{number!r}: '{parsed.service}' is not a recognised service code"
        )
    if parsed.size_in not in NOMINAL_SIZES:
        issues.warnings.append(
            f"{number!r}: {parsed.size_in}\" is not a standard nominal pipe size"
        )
    if not parsed.spec:
        issues.warnings.append(f"{number!r}: no piping spec code — the line cannot be procured from this")
    elif parsed.spec not in PIPE_SPECS:
        issues.warnings.append(f"{number!r}: '{parsed.spec}' is not a recognised piping spec code")
    return issues
