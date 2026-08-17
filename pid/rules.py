"""Deterministic validation of a :class:`~pid.model.PIDModel`.

The agents in :mod:`pid.agents` propose a drawing; this module decides whether
the drawing holds up. Nothing here calls a model, so the same checks run in CI,
in tests, and on a hand-written P&ID with no API key present.

Rules are grouped by number so a finding's ID tells you what kind of problem it
is:

===========  ==============================================================
``R0xx``     Referential integrity — tags that point at nothing
``R1xx``     Tag conventions — ISA-5.1 letters, line numbers, prefixes
``R2xx``     Topology — connectivity of the equipment graph
``R3xx``     Equipment completeness — instrumentation and protection
``R4xx``     Control and safety logic — loops, valves, relief devices
``R5xx``     Line completeness — data needed to procure and build the line
===========  ==============================================================

Severity is a judgement about *drawing* quality, not plant safety: an ``error``
means the P&ID is internally inconsistent or missing something no competent
reviewer would sign off on; a ``warning`` means it is very likely wrong but
there are legitimate designs where it isn't.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from collections.abc import Callable, Iterator

from .model import PIDModel
from .standards import (
    BLOCK_VALVE_TYPES,
    INLINE_TYPES,
    INSTRUMENT_LOCATIONS,
    INVENTORY_EQUIPMENT,
    RELIEF_DEVICE_TYPES,
    RELIEF_REQUIRED_EQUIPMENT,
    ROTATING_EQUIPMENT,
    SIGNAL_TYPES,
    EQUIPMENT_PREFIXES,
)
from .tagging import (
    parse_instrument_tag,
    parse_line_number,
    validate_equipment_tag,
    validate_instrument_tag,
    validate_line_number,
)


class Severity(StrEnum):
    """How badly a finding reflects on the drawing.

    A judgement about drawing quality, not plant safety: an ``ERROR`` means the
    P&ID is internally inconsistent or missing something no competent reviewer
    would sign off on; a ``WARNING`` means it is very likely wrong but there are
    legitimate designs where it isn't.
    """

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Finding:
    """One problem found in a drawing."""

    rule_id: str
    severity: Severity
    subject: str
    message: str
    suggestion: str = ""

    def __str__(self) -> str:
        line = f"[{self.severity.value.upper():7}] {self.rule_id} {self.subject}: {self.message}"
        if self.suggestion:
            line += f"\n{' ' * 10}-> {self.suggestion}"
        return line


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.INFO]

    @property
    def ok(self) -> bool:
        """True when nothing rises to the level of an error."""
        return not self.errors

    def counts(self) -> dict[str, int]:
        return {
            "error": len(self.errors),
            "warning": len(self.warnings),
            "info": len(self.infos),
        }

    def summary(self) -> str:
        c = self.counts()
        return f"{c['error']} error(s), {c['warning']} warning(s), {c['info']} note(s)"

    def to_text(self) -> str:
        if not self.findings:
            return "No findings."
        order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
        ranked = sorted(self.findings, key=lambda f: (order[f.severity], f.rule_id, f.subject))
        return "\n".join(str(f) for f in ranked)

    def to_dicts(self) -> list[dict[str, str]]:
        """Flatten for JSON output or for feeding back to the review agent."""
        return [
            {
                "rule_id": f.rule_id,
                "severity": f.severity.value,
                "subject": f.subject,
                "message": f.message,
                "suggestion": f.suggestion,
            }
            for f in self.findings
        ]


# A rule takes the model and yields findings.
Rule = Callable[[PIDModel], Iterator[Finding]]
_REGISTRY: list[tuple[str, str, Rule]] = []


def rule(rule_id: str, title: str):
    """Register a validation rule."""

    def decorator(fn: Rule) -> Rule:
        _REGISTRY.append((rule_id, title, fn))
        return fn

    return decorator


def registered_rules() -> list[tuple[str, str]]:
    """(id, title) for every rule, for `pid rules --list`."""
    return [(rule_id, title) for rule_id, title, _ in _REGISTRY]


def validate(model: PIDModel) -> ValidationReport:
    """Run every registered rule against a model."""
    report = ValidationReport()
    for _, _, fn in _REGISTRY:
        report.findings.extend(fn(model))
    return report


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _line_numbers(model: PIDModel) -> set[str]:
    return {s.number for s in model.streams}


def _equipment_tags(model: PIDModel) -> set[str]:
    return {e.tag for e in model.equipment}


def _instrument_tags(model: PIDModel) -> set[str]:
    tags = {i.tag for i in model.instruments}
    tags |= {item.tag for _, item in model.inline_items() if item.tag}
    return tags


def _has_variable(model: PIDModel, equipment_tag: str, variable: str) -> bool:
    """True if any instrument on this equipment measures the given variable."""
    for inst in model.instruments_on(equipment_tag):
        parsed = parse_instrument_tag(inst.tag)
        if parsed and parsed.variable == variable:
            return True
    return False


def _relief_devices_protecting(model: PIDModel, equipment_tag: str) -> list[str]:
    """Relief device tags protecting an equipment item.

    A device counts as protecting the item if it is an in-line device on a line
    leaving that item, or an instrument-style tag (``PSV-…``) attached to it.
    """
    found: list[str] = []
    for stream in model.streams_from(equipment_tag):
        for item in stream.inline:
            if item.type in RELIEF_DEVICE_TYPES:
                found.append(item.tag or f"{item.type} on {stream.number}")
    for inst in model.instruments_on(equipment_tag):
        parsed = parse_instrument_tag(inst.tag)
        if parsed and parsed.is_relief_device:
            found.append(inst.tag)
    return found


def _has_block_valve(streams) -> bool:
    return any(item.type in BLOCK_VALVE_TYPES for s in streams for item in s.inline)


# ---------------------------------------------------------------------------
# R0xx — referential integrity
# ---------------------------------------------------------------------------


@rule("R001", "Tags are unique across the drawing")
def _unique_tags(model: PIDModel) -> Iterator[Finding]:
    for tag, count in Counter(model.all_tags()).items():
        if count > 1:
            yield Finding(
                "R001",
                Severity.ERROR,
                tag,
                f"tag appears {count} times; every tag on a P&ID must be unique",
                "renumber the duplicates, or delete the one that was added twice",
            )


@rule("R002", "Line endpoints reference equipment that exists")
def _endpoints_exist(model: PIDModel) -> Iterator[Finding]:
    known = _equipment_tags(model)
    for stream in model.streams:
        for role, endpoint in (("source", stream.source), ("destination", stream.destination)):
            if endpoint.equipment not in known:
                yield Finding(
                    "R002",
                    Severity.ERROR,
                    stream.number,
                    f"{role} equipment {endpoint.equipment!r} is not in the equipment list",
                    "add the equipment item, or add it as a boundary connector if it is off-page",
                )


@rule("R003", "Line endpoints reference ports that exist")
def _ports_exist(model: PIDModel) -> Iterator[Finding]:
    for stream in model.streams:
        for role, endpoint in (("source", stream.source), ("destination", stream.destination)):
            if endpoint.port is None:
                continue
            equipment = model.equipment_by_tag(endpoint.equipment)
            if equipment is None or not equipment.ports:
                continue
            if equipment.port(endpoint.port) is None:
                available = ", ".join(p.name for p in equipment.ports) or "none defined"
                yield Finding(
                    "R003",
                    Severity.WARNING,
                    stream.number,
                    f"{role} port {endpoint.port!r} does not exist on {equipment.tag} (has: {available})",
                    f"add the nozzle to {equipment.tag} or correct the port name",
                )


@rule("R004", "Instruments are attached to something that exists")
def _instrument_attachment_exists(model: PIDModel) -> Iterator[Finding]:
    valid = _equipment_tags(model) | _line_numbers(model)
    for inst in model.instruments:
        if inst.attached_to not in valid:
            yield Finding(
                "R004",
                Severity.ERROR,
                inst.tag,
                f"attached to {inst.attached_to!r}, which is neither an equipment tag nor a line number",
                "point attached_to at an equipment tag or a full line number",
            )


@rule("R005", "Control loops reference instruments that exist")
def _loop_references_exist(model: PIDModel) -> Iterator[Finding]:
    known = _instrument_tags(model)
    for loop in model.loops:
        referenced = [loop.controller_tag, loop.final_element_tag, *loop.measurement_tags]
        for tag in referenced:
            if tag and tag not in known:
                yield Finding(
                    "R005",
                    Severity.ERROR,
                    f"loop {loop.number}",
                    f"references {tag!r}, which is not in the instrument list or on any line",
                    f"add {tag} as an instrument, or as an in-line component if it is a valve",
                )


@rule("R006", "Interlocks reference tags that exist")
def _interlock_references_exist(model: PIDModel) -> Iterator[Finding]:
    known = _instrument_tags(model) | _equipment_tags(model)
    for interlock in model.interlocks:
        for initiator in interlock.initiators:
            if initiator not in known:
                yield Finding(
                    "R006",
                    Severity.WARNING,
                    interlock.tag,
                    f"initiator {initiator!r} is not a tag on this drawing",
                    "add the initiating instrument, or note that it is on another drawing",
                )
        if not interlock.initiators:
            yield Finding(
                "R006",
                Severity.ERROR,
                interlock.tag,
                "has no initiator — nothing causes this interlock to act",
                "list the instrument tags that trip it",
            )
        if not interlock.actions:
            yield Finding(
                "R006",
                Severity.ERROR,
                interlock.tag,
                "has no actions — the interlock trips but does nothing",
                "list what it closes, stops or opens",
            )


# ---------------------------------------------------------------------------
# R1xx — tag conventions
# ---------------------------------------------------------------------------


@rule("R101", "Instrument tags follow ISA-5.1")
def _instrument_tags_valid(model: PIDModel) -> Iterator[Finding]:
    for inst in model.instruments:
        issues = validate_instrument_tag(inst.tag)
        for message in issues.errors:
            yield Finding("R101", Severity.ERROR, inst.tag, message, "see ISA-5.1 Table 1")
        for message in issues.warnings:
            yield Finding("R101", Severity.WARNING, inst.tag, message, "see ISA-5.1 Table 1")


@rule("R102", "Equipment tags follow the project prefix convention")
def _equipment_tags_valid(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.process_equipment():
        issues = validate_equipment_tag(equipment.tag, equipment.kind)
        for message in issues.errors:
            yield Finding("R102", Severity.ERROR, equipment.tag, message)
        for message in issues.warnings:
            yield Finding("R102", Severity.WARNING, equipment.tag, message)


@rule("R103", "Line numbers are complete and well formed")
def _line_numbers_valid(model: PIDModel) -> Iterator[Finding]:
    for stream in model.streams:
        issues = validate_line_number(stream.number)
        for message in issues.errors:
            yield Finding("R103", Severity.ERROR, stream.number, message)
        for message in issues.warnings:
            yield Finding("R103", Severity.WARNING, stream.number, message)


@rule("R104", "Equipment class is a recognised one")
def _equipment_kind_known(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.equipment:
        if equipment.kind not in EQUIPMENT_PREFIXES:
            yield Finding(
                "R104",
                Severity.WARNING,
                equipment.tag,
                f"equipment class {equipment.kind!r} is not one the renderer has a symbol for",
                f"use one of: {', '.join(sorted(EQUIPMENT_PREFIXES))}",
            )


@rule("R105", "In-line component types are recognised")
def _inline_types_known(model: PIDModel) -> Iterator[Finding]:
    for stream, item in model.inline_items():
        if item.type not in INLINE_TYPES:
            yield Finding(
                "R105",
                Severity.WARNING,
                item.tag or stream.number,
                f"in-line component type {item.type!r} is not recognised",
                f"use one of: {', '.join(sorted(INLINE_TYPES))}",
            )


@rule("R106", "Instrument location and signal codes are recognised")
def _instrument_codes_known(model: PIDModel) -> Iterator[Finding]:
    for inst in model.instruments:
        if inst.location not in INSTRUMENT_LOCATIONS:
            yield Finding(
                "R106",
                Severity.WARNING,
                inst.tag,
                f"location {inst.location!r} is not a recognised ISA readout location",
                f"use one of: {', '.join(sorted(INSTRUMENT_LOCATIONS))}",
            )
        if inst.signal not in SIGNAL_TYPES:
            yield Finding(
                "R106",
                Severity.WARNING,
                inst.tag,
                f"signal type {inst.signal!r} is not recognised",
                f"use one of: {', '.join(sorted(SIGNAL_TYPES))}",
            )


@rule("R107", "Instruments in a loop share the loop number")
def _loop_numbers_consistent(model: PIDModel) -> Iterator[Finding]:
    for loop in model.loops:
        for tag in [loop.controller_tag, loop.final_element_tag, *loop.measurement_tags]:
            parsed = parse_instrument_tag(tag) if tag else None
            if parsed and parsed.number != loop.number:
                yield Finding(
                    "R107",
                    Severity.WARNING,
                    tag,
                    f"belongs to loop {loop.number} but is numbered {parsed.number}",
                    f"renumber to {parsed.letters}-{loop.number} so the loop reads as one unit",
                )


# ---------------------------------------------------------------------------
# R2xx — topology
# ---------------------------------------------------------------------------


@rule("R201", "No orphan equipment")
def _no_orphans(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.equipment:
        if not model.streams_from(equipment.tag) and not model.streams_to(equipment.tag):
            severity = Severity.WARNING if equipment.is_boundary else Severity.ERROR
            yield Finding(
                "R201",
                severity,
                equipment.tag,
                "has no lines connected to it",
                "connect it, or remove it from the equipment list",
            )


@rule("R202", "Pumps and compressors have both a suction and a discharge")
def _machines_connected(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.equipment:
        if equipment.kind not in {"pump", "compressor", "blower"}:
            continue
        if not model.streams_to(equipment.tag):
            yield Finding(
                "R202",
                Severity.ERROR,
                equipment.tag,
                f"{equipment.kind} has no suction line",
                "add the line feeding it",
            )
        if not model.streams_from(equipment.tag):
            yield Finding(
                "R202",
                Severity.ERROR,
                equipment.tag,
                f"{equipment.kind} has no discharge line",
                "add the line it delivers into",
            )


@rule("R203", "Process flow does not dead-end unexpectedly")
def _no_dead_ends(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.process_equipment():
        if model.streams_from(equipment.tag):
            continue
        if not model.streams_to(equipment.tag):
            continue  # already reported as an orphan by R201
        if equipment.kind in INVENTORY_EQUIPMENT:
            continue  # a tank is allowed to be a terminus
        yield Finding(
            "R203",
            Severity.WARNING,
            equipment.tag,
            f"material flows into this {equipment.kind} but nothing leaves it",
            "add the outlet line, or a boundary connector if it continues on another drawing",
        )


@rule("R204", "Lines do not start and end on the same nozzle")
def _no_degenerate_streams(model: PIDModel) -> Iterator[Finding]:
    for stream in model.streams:
        same_equipment = stream.source.equipment == stream.destination.equipment
        same_port = stream.source.port == stream.destination.port
        if same_equipment and same_port:
            yield Finding(
                "R204",
                Severity.ERROR,
                stream.number,
                f"starts and ends at the same point on {stream.source.equipment}",
                "correct one end; a recirculation line must return to a different nozzle",
            )
        elif same_equipment:
            yield Finding(
                "R204",
                Severity.INFO,
                stream.number,
                f"is a recirculation line on {stream.source.equipment}",
                "confirm this is intentional",
            )


# ---------------------------------------------------------------------------
# R3xx — equipment completeness
# ---------------------------------------------------------------------------


@rule("R301", "Inventory equipment has level measurement")
def _level_on_inventory(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.equipment:
        if equipment.kind not in INVENTORY_EQUIPMENT:
            continue
        if _has_variable(model, equipment.tag, "L"):
            continue
        yield Finding(
            "R301",
            Severity.ERROR,
            equipment.tag,
            f"{equipment.kind} holds inventory but has no level instrument",
            f"add a level transmitter (e.g. LT) and indication on {equipment.tag}",
        )


@rule("R302", "Pressure-retaining equipment has overpressure protection")
def _relief_on_pressure_equipment(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.equipment:
        if equipment.kind not in RELIEF_REQUIRED_EQUIPMENT:
            continue
        if _relief_devices_protecting(model, equipment.tag):
            continue
        yield Finding(
            "R302",
            Severity.ERROR,
            equipment.tag,
            f"{equipment.kind} has no relief device",
            f"add a relief valve or rupture disc on a line from {equipment.tag}, "
            "or note on the drawing why it cannot be over-pressured",
        )


@rule("R303", "Equipment carries its design conditions")
def _design_conditions_present(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.process_equipment():
        missing = []
        if equipment.design_pressure_psig is None:
            missing.append("design pressure")
        if equipment.design_temp_f is None:
            missing.append("design temperature")
        if missing:
            yield Finding(
                "R303",
                Severity.WARNING,
                equipment.tag,
                f"missing {' and '.join(missing)}",
                "a P&ID is not issuable for design without design conditions on every item",
            )


@rule("R304", "Heat exchangers have outlet temperature indication")
def _hx_temperature(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.equipment:
        if equipment.kind != "heat_exchanger":
            continue
        outlet_lines = [s.number for s in model.streams_from(equipment.tag)]
        has_temperature = _has_variable(model, equipment.tag, "T") or any(
            _has_variable(model, line, "T") for line in outlet_lines
        )
        if not has_temperature:
            yield Finding(
                "R304",
                Severity.WARNING,
                equipment.tag,
                "no temperature indication on or downstream of the exchanger",
                "add a temperature element on the process outlet — the duty cannot be checked without it",
            )


@rule("R305", "Rotating equipment can be isolated for maintenance")
def _isolation_on_machines(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.equipment:
        if equipment.kind not in ROTATING_EQUIPMENT:
            continue
        inlets = model.streams_to(equipment.tag)
        outlets = model.streams_from(equipment.tag)
        if inlets and not _has_block_valve(inlets):
            yield Finding(
                "R305",
                Severity.WARNING,
                equipment.tag,
                f"no block valve on the {equipment.kind} inlet",
                "add a suction isolation valve so the machine can be worked on",
            )
        if outlets and not _has_block_valve(outlets):
            yield Finding(
                "R305",
                Severity.WARNING,
                equipment.tag,
                f"no block valve on the {equipment.kind} outlet",
                "add a discharge isolation valve",
            )


@rule("R306", "Centrifugal machines have a non-return device on discharge")
def _check_valve_on_discharge(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.equipment:
        if equipment.kind not in {"pump", "compressor", "blower"}:
            continue
        outlets = model.streams_from(equipment.tag)
        if not outlets:
            continue  # R202 already covers this
        if any(item.type == "check_valve" for s in outlets for item in s.inline):
            continue
        yield Finding(
            "R306",
            Severity.WARNING,
            equipment.tag,
            "no check valve on the discharge",
            "add a check valve so a trip cannot back-flow the line through the machine",
        )


@rule("R307", "Pump discharge pressure is indicated")
def _pump_discharge_pressure(model: PIDModel) -> Iterator[Finding]:
    for equipment in model.equipment:
        if equipment.kind != "pump":
            continue
        outlets = [s.number for s in model.streams_from(equipment.tag)]
        if _has_variable(model, equipment.tag, "P") or any(
            _has_variable(model, line, "P") for line in outlets
        ):
            continue
        yield Finding(
            "R307",
            Severity.INFO,
            equipment.tag,
            "no pressure indication on the discharge",
            "operators normally want a discharge gauge (PI) to see that the pump is making head",
        )


# ---------------------------------------------------------------------------
# R4xx — control and safety logic
# ---------------------------------------------------------------------------


@rule("R401", "Control valves declare a fail position")
def _control_valve_fail_position(model: PIDModel) -> Iterator[Finding]:
    for stream, item in model.inline_items():
        if item.type != "control_valve":
            continue
        if item.fail_position is None:
            yield Finding(
                "R401",
                Severity.ERROR,
                item.tag or f"control valve on {stream.number}",
                "no fail position given",
                "state fail_open, fail_closed or fail_last — this is a safety decision, "
                "not a detail to leave to the valve vendor",
            )


@rule("R402", "Every control valve belongs to a loop")
def _control_valve_has_loop(model: PIDModel) -> Iterator[Finding]:
    controlled = {loop.final_element_tag for loop in model.loops}
    for stream, item in model.inline_items():
        if item.type != "control_valve":
            continue
        if item.tag is None:
            yield Finding(
                "R402",
                Severity.ERROR,
                stream.number,
                "control valve has no tag",
                "tag it after the loop it serves, e.g. 'FV-1201'",
            )
        elif item.tag not in controlled:
            yield Finding(
                "R402",
                Severity.WARNING,
                item.tag,
                "is a control valve but no control loop drives it",
                f"add a loop whose final_element_tag is {item.tag}, or make it a manual valve",
            )


@rule("R403", "Control loops are closed")
def _loops_closed(model: PIDModel) -> Iterator[Finding]:
    for loop in model.loops:
        if not loop.measurement_tags:
            yield Finding(
                "R403",
                Severity.ERROR,
                f"loop {loop.number}",
                "has no measurement — a controller with no input cannot control",
                "add the sensing element and transmitter tags",
            )
        if not loop.final_element_tag:
            yield Finding(
                "R403",
                Severity.ERROR,
                f"loop {loop.number}",
                "has no final control element",
                "add the control valve, damper or drive the controller moves",
            )
        controller = parse_instrument_tag(loop.controller_tag) if loop.controller_tag else None
        if controller is None:
            yield Finding(
                "R403",
                Severity.ERROR,
                f"loop {loop.number}",
                f"controller tag {loop.controller_tag!r} is not a valid instrument tag",
            )
        elif not controller.is_controller:
            yield Finding(
                "R403",
                Severity.WARNING,
                loop.controller_tag,
                "is named as the loop controller but its tag has no 'C' function letter",
                f"a controller is tagged like '{controller.variable}IC-{loop.number}'",
            )


@rule("R404", "Cascade loops point at a real master")
def _cascade_master_exists(model: PIDModel) -> Iterator[Finding]:
    numbers = {loop.number for loop in model.loops}
    for loop in model.loops:
        if loop.cascade_from and loop.cascade_from not in numbers:
            yield Finding(
                "R404",
                Severity.WARNING,
                f"loop {loop.number}",
                f"cascades from loop {loop.cascade_from}, which is not on this drawing",
                "add the master loop, or note that it lives on another drawing",
            )


@rule("R405", "Relief devices are sized and set")
def _relief_set_pressure(model: PIDModel) -> Iterator[Finding]:
    for stream, item in model.inline_items():
        if item.type not in RELIEF_DEVICE_TYPES:
            continue
        subject = item.tag or f"{item.type} on {stream.number}"
        if item.set_pressure_psig is None:
            yield Finding(
                "R405",
                Severity.WARNING,
                subject,
                "no set pressure given",
                "state the set pressure; without it the device cannot be specified",
            )
            continue
        protected = model.equipment_by_tag(stream.source.equipment)
        if protected is None or protected.design_pressure_psig is None:
            continue
        if item.set_pressure_psig > protected.design_pressure_psig:
            yield Finding(
                "R405",
                Severity.ERROR,
                subject,
                f"set at {item.set_pressure_psig:g} psig, above the "
                f"{protected.design_pressure_psig:g} psig design pressure of {protected.tag}",
                "the set pressure must not exceed the MAWP of the equipment it protects "
                "(ASME VIII Div. 1 UG-134)",
            )


#: Words that mark an equipment item as somewhere relief may legitimately
#: discharge. Deliberately a name check: nothing in the model distinguishes a
#: knockout drum from any other vessel, and treating every vessel as a valid
#: relief destination would let a PSV discharge into the feed tank unremarked.
_COLLECTION_HINTS = (
    "vent",
    "flare",
    "knockout",
    "knock-out",
    "knock out",
    "blowdown",
    "blow-down",
    "catch",
    "quench",
    "scrubber",
    "atmosphere",
    "relief header",
)


@rule("R406", "Relief device discharges are routed somewhere they can relieve to")
def _relief_discharge_routed(model: PIDModel) -> Iterator[Finding]:
    for stream, item in model.inline_items():
        if item.type not in RELIEF_DEVICE_TYPES:
            continue
        destination = model.equipment_by_tag(stream.destination.equipment)
        if destination is None:
            continue  # R002 covers this
        name = destination.name.lower()
        if destination.is_boundary or any(hint in name for hint in _COLLECTION_HINTS):
            continue
        yield Finding(
            "R406",
            Severity.WARNING,
            item.tag or stream.number,
            f"relief discharge lands on {destination.tag} ({destination.name}), which is "
            "not a vent, flare, knockout drum or off-page connector",
            "route the discharge to a collection system or off-page connector — "
            "relieving into process equipment moves the overpressure rather than removing it",
        )


@rule("R407", "Trip and alarm switches have a setpoint")
def _switch_setpoints(model: PIDModel) -> Iterator[Finding]:
    for inst in model.instruments:
        parsed = parse_instrument_tag(inst.tag)
        if parsed is None or parsed.is_relief_device:
            continue
        if not (parsed.is_switch or parsed.is_alarm):
            continue
        if not inst.setpoint:
            yield Finding(
                "R407",
                Severity.WARNING,
                inst.tag,
                "is a switch or alarm with no setpoint",
                "state the trip point; a switch without one cannot be commissioned",
            )


@rule("R408", "Trip switches drive an interlock")
def _switches_in_interlocks(model: PIDModel) -> Iterator[Finding]:
    initiators = {tag for interlock in model.interlocks for tag in interlock.initiators}
    for inst in model.instruments:
        parsed = parse_instrument_tag(inst.tag)
        if parsed is None or parsed.is_relief_device or not parsed.is_switch:
            continue
        if parsed.is_alarm:
            continue  # an alarm-only switch just annunciates
        if inst.tag not in initiators:
            yield Finding(
                "R408",
                Severity.INFO,
                inst.tag,
                "is a switch that no interlock uses",
                "either add the interlock it initiates, or retag it as an alarm (…A…) "
                "if it only annunciates",
            )


# ---------------------------------------------------------------------------
# R5xx — line completeness
# ---------------------------------------------------------------------------


@rule("R501", "Lines carry a size")
def _line_size(model: PIDModel) -> Iterator[Finding]:
    for stream in model.streams:
        if stream.size_in is None:
            yield Finding(
                "R501",
                Severity.WARNING,
                stream.number,
                "no nominal size",
                "set size_in so the line list is procurable",
            )


@rule("R502", "Line size agrees with the line number")
def _line_size_matches_number(model: PIDModel) -> Iterator[Finding]:
    for stream in model.streams:
        parsed = parse_line_number(stream.number)
        if parsed is None or stream.size_in is None:
            continue
        if abs(parsed.size_in - stream.size_in) > 1e-6:
            yield Finding(
                "R502",
                Severity.ERROR,
                stream.number,
                f"line number says {parsed.size_in:g}\" but size_in is {stream.size_in:g}\"",
                "make the two agree — downstream takeoffs read whichever they happen to use",
            )


@rule("R503", "Lines say what phase they carry")
def _line_phase(model: PIDModel) -> Iterator[Finding]:
    for stream in model.streams:
        if stream.phase is None:
            yield Finding(
                "R503",
                Severity.INFO,
                stream.number,
                "no phase given",
                "phase drives line sizing and slope requirements",
            )


@rule("R504", "Line service agrees with the line number")
def _line_service_matches(model: PIDModel) -> Iterator[Finding]:
    for stream in model.streams:
        parsed = parse_line_number(stream.number)
        if parsed is None:
            continue
        if parsed.service != stream.service:
            yield Finding(
                "R504",
                Severity.WARNING,
                stream.number,
                f"line number service is {parsed.service!r} but the service field says {stream.service!r}",
                "make the two agree",
            )
