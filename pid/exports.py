"""Tabular deliverables extracted from a P&ID.

A P&ID is only half the package — the other half is the schedules everyone
downstream actually works from: piping buys off the line list, instrumentation
off the instrument index, and process safety off the relief-device and interlock
schedules. All five are derived from the same model, so they cannot drift out of
step with the drawing.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from .model import PIDModel
from .standards import PIPE_SPECS, RELIEF_DEVICE_TYPES, SERVICE_CODES
from .tagging import describe_instrument_tag, parse_instrument_tag


def _csv(rows: list[dict[str, object]], columns: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: ("" if row.get(key) is None else row.get(key)) for key in columns})
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Equipment list
# ---------------------------------------------------------------------------

EQUIPMENT_COLUMNS = [
    "tag",
    "name",
    "kind",
    "capacity",
    "material",
    "design_pressure_psig",
    "design_temp_f",
    "insulated",
    "spared_by",
    "inlet_lines",
    "outlet_lines",
    "notes",
]


def equipment_rows(model: PIDModel) -> list[dict[str, object]]:
    rows = []
    for equipment in model.process_equipment():
        rows.append(
            {
                "tag": equipment.tag,
                "name": equipment.name,
                "kind": equipment.kind,
                "capacity": equipment.capacity,
                "material": equipment.material,
                "design_pressure_psig": equipment.design_pressure_psig,
                "design_temp_f": equipment.design_temp_f,
                "insulated": "Y" if equipment.insulated else "N",
                "spared_by": equipment.spared_by,
                "inlet_lines": "; ".join(s.number for s in model.streams_to(equipment.tag)),
                "outlet_lines": "; ".join(s.number for s in model.streams_from(equipment.tag)),
                "notes": equipment.notes,
            }
        )
    return sorted(rows, key=lambda r: str(r["tag"]))


def equipment_list_csv(model: PIDModel) -> str:
    return _csv(equipment_rows(model), EQUIPMENT_COLUMNS)


# ---------------------------------------------------------------------------
# Line list
# ---------------------------------------------------------------------------

LINE_COLUMNS = [
    "line_number",
    "size_in",
    "service",
    "service_description",
    "spec",
    "spec_description",
    "from",
    "from_port",
    "to",
    "to_port",
    "phase",
    "design_flow",
    "insulated",
    "traced",
    "inline_components",
    "description",
    "notes",
]


def line_rows(model: PIDModel) -> list[dict[str, object]]:
    rows = []
    for stream in model.streams:
        components = "; ".join(
            f"{item.tag} ({item.type})" if item.tag else item.type for item in stream.inline
        )
        rows.append(
            {
                "line_number": stream.number,
                "size_in": stream.size_in,
                "service": stream.service,
                "service_description": SERVICE_CODES.get(stream.service, ""),
                "spec": stream.spec,
                "spec_description": PIPE_SPECS.get(stream.spec, ""),
                "from": stream.source.equipment,
                "from_port": stream.source.port,
                "to": stream.destination.equipment,
                "to_port": stream.destination.port,
                "phase": stream.phase,
                "design_flow": stream.design_flow,
                "insulated": "Y" if stream.insulated else "N",
                "traced": "Y" if stream.traced else "N",
                "inline_components": components,
                "description": stream.description,
                "notes": stream.notes,
            }
        )
    return sorted(rows, key=lambda r: str(r["line_number"]))


def line_list_csv(model: PIDModel) -> str:
    return _csv(line_rows(model), LINE_COLUMNS)


# ---------------------------------------------------------------------------
# Instrument index
# ---------------------------------------------------------------------------

INSTRUMENT_COLUMNS = [
    "tag",
    "function",
    "description",
    "attached_to",
    "loop",
    "location",
    "signal",
    "range_low",
    "range_high",
    "units",
    "setpoint",
    "notes",
]


def instrument_rows(model: PIDModel) -> list[dict[str, object]]:
    rows = []
    for instrument in model.instruments:
        parsed = parse_instrument_tag(instrument.tag)
        rows.append(
            {
                "tag": instrument.tag,
                "function": describe_instrument_tag(instrument.tag),
                "description": instrument.description,
                "attached_to": instrument.attached_to,
                "loop": instrument.loop or (parsed.number if parsed else ""),
                "location": instrument.location,
                "signal": instrument.signal,
                "range_low": instrument.range_low,
                "range_high": instrument.range_high,
                "units": instrument.units,
                "setpoint": instrument.setpoint,
                "notes": instrument.notes,
            }
        )
    return sorted(rows, key=lambda r: str(r["tag"]))


def instrument_index_csv(model: PIDModel) -> str:
    return _csv(instrument_rows(model), INSTRUMENT_COLUMNS)


# ---------------------------------------------------------------------------
# Control loop schedule
# ---------------------------------------------------------------------------

LOOP_COLUMNS = [
    "loop",
    "description",
    "controlled_variable",
    "measurements",
    "controller",
    "final_element",
    "fail_position",
    "action",
    "setpoint",
    "cascade_from",
    "notes",
]


def loop_rows(model: PIDModel) -> list[dict[str, object]]:
    # Fail position lives on the valve, but the loop schedule is where a
    # reviewer looks for it, so join it across here.
    fail_positions = {
        item.tag: item.fail_position for _, item in model.inline_items() if item.tag
    }
    rows = []
    for loop in model.loops:
        rows.append(
            {
                "loop": loop.number,
                "description": loop.description,
                "controlled_variable": loop.controlled_variable,
                "measurements": "; ".join(loop.measurement_tags),
                "controller": loop.controller_tag,
                "final_element": loop.final_element_tag,
                "fail_position": fail_positions.get(loop.final_element_tag),
                "action": loop.action,
                "setpoint": loop.setpoint,
                "cascade_from": loop.cascade_from,
                "notes": loop.notes,
            }
        )
    return sorted(rows, key=lambda r: str(r["loop"]))


def loop_schedule_csv(model: PIDModel) -> str:
    return _csv(loop_rows(model), LOOP_COLUMNS)


# ---------------------------------------------------------------------------
# Relief device and interlock schedules
# ---------------------------------------------------------------------------

RELIEF_COLUMNS = [
    "tag",
    "type",
    "on_line",
    "protects",
    "set_pressure_psig",
    "equipment_design_pressure_psig",
    "discharges_to",
    "notes",
]


def relief_rows(model: PIDModel) -> list[dict[str, object]]:
    rows = []
    for stream, item in model.inline_items():
        if item.type not in RELIEF_DEVICE_TYPES:
            continue
        protected = model.equipment_by_tag(stream.source.equipment)
        rows.append(
            {
                "tag": item.tag,
                "type": item.type,
                "on_line": stream.number,
                "protects": stream.source.equipment,
                "set_pressure_psig": item.set_pressure_psig,
                "equipment_design_pressure_psig": (
                    protected.design_pressure_psig if protected else None
                ),
                "discharges_to": stream.destination.equipment,
                "notes": item.notes,
            }
        )
    return sorted(rows, key=lambda r: str(r["tag"] or ""))


def relief_schedule_csv(model: PIDModel) -> str:
    return _csv(relief_rows(model), RELIEF_COLUMNS)


INTERLOCK_COLUMNS = ["tag", "description", "initiators", "trip_setpoint", "actions", "reset", "sil"]


def interlock_rows(model: PIDModel) -> list[dict[str, object]]:
    return sorted(
        (
            {
                "tag": interlock.tag,
                "description": interlock.description,
                "initiators": "; ".join(interlock.initiators),
                "trip_setpoint": interlock.trip_setpoint,
                "actions": "; ".join(interlock.actions),
                "reset": interlock.reset,
                "sil": interlock.sil,
            }
            for interlock in model.interlocks
        ),
        key=lambda r: str(r["tag"]),
    )


def interlock_schedule_csv(model: PIDModel) -> str:
    return _csv(interlock_rows(model), INTERLOCK_COLUMNS)


# ---------------------------------------------------------------------------
# Write the whole package
# ---------------------------------------------------------------------------

EXPORTERS = {
    "equipment_list.csv": equipment_list_csv,
    "line_list.csv": line_list_csv,
    "instrument_index.csv": instrument_index_csv,
    "loop_schedule.csv": loop_schedule_csv,
    "relief_schedule.csv": relief_schedule_csv,
    "interlock_schedule.csv": interlock_schedule_csv,
}


def write_package(model: PIDModel, directory: str | Path, stem: str = "") -> list[Path]:
    """Write every schedule as CSV into ``directory``. Returns the paths written."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, exporter in EXPORTERS.items():
        content = exporter(model)
        # Skip schedules with no rows beyond the header — an empty interlock
        # schedule is noise, not information.
        if content.count("\n") <= 1:
            continue
        path = target / (f"{stem}_{filename}" if stem else filename)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
