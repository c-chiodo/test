"""The P&ID data model.

This is the single source of truth that everything else in the package reads:
the agents produce it, the rule engine checks it, the renderer draws it, and
the exporters flatten it into line lists and instrument indices.

Design notes
------------
* Off-page connections are modelled as ``Equipment`` with ``kind="boundary"``,
  so the whole drawing is one uniform equipment-to-equipment graph. Nothing
  downstream needs a special case for "this line goes off the page".
* Fields the agents fill in are deliberately loosely typed (``str`` rather than
  ``Literal``) wherever the vocabulary is long. An out-of-vocabulary value
  should surface as a validation *finding*, not as a parse exception that
  throws away an otherwise good drawing. Tight ``Literal`` types are reserved
  for small, closed sets.
* Numeric ranges are likewise unconstrained here. ``pid.rules`` decides what
  counts as a sane design pressure; the schema's job is only to carry it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PortKind = Literal["inlet", "outlet", "utility_in", "utility_out", "vent", "drain", "relief"]
Phase = Literal["liquid", "gas", "vapor", "two_phase", "slurry", "solid", "steam"]
FailPosition = Literal["fail_open", "fail_closed", "fail_last", "fail_indeterminate"]
ControlAction = Literal["direct", "reverse"]


class Port(BaseModel):
    """A nozzle or connection point on an equipment item."""

    name: str = Field(description="Short port name unique within the equipment, e.g. 'N1' or 'suction'.")
    kind: PortKind = Field(description="Role of the connection.")
    description: str = Field(default="", description="What connects here, e.g. 'crude oil feed'.")
    size_in: float | None = Field(default=None, description="Nozzle nominal size in inches.")


class Equipment(BaseModel):
    """A tagged equipment item, or an off-page boundary connector."""

    tag: str = Field(description="Equipment tag, e.g. 'P-1201A' or 'TK-1101'.")
    name: str = Field(description="Service description, e.g. 'Crude oil feed pump'.")
    kind: str = Field(
        description=(
            "Equipment class. Use one of: pump, tank, vessel, reactor, column, "
            "heat_exchanger, filter, compressor, blower, centrifuge, dryer, mixer, "
            "conveyor, scale, separator, boundary."
        )
    )
    ports: list[Port] = Field(default_factory=list, description="Nozzles on this item.")
    design_pressure_psig: float | None = Field(default=None, description="Design pressure, psig.")
    design_temp_f: float | None = Field(default=None, description="Design temperature, degrees F.")
    capacity: str = Field(default="", description="Duty or size, e.g. '150 gpm @ 120 ft TDH' or '12,000 gal'.")
    material: str = Field(default="", description="Material of construction, e.g. '316L SS'.")
    insulated: bool = Field(default=False, description="True if insulated or steam traced.")
    spared_by: str | None = Field(default=None, description="Tag of the installed spare, if any.")
    notes: str = Field(default="", description="Anything a reviewer needs to know.")

    @property
    def is_boundary(self) -> bool:
        return self.kind == "boundary"

    def port(self, name: str) -> Port | None:
        return next((p for p in self.ports if p.name == name), None)


class InlineItem(BaseModel):
    """A valve, fitting or in-line device sitting on a line, in flow order."""

    type: str = Field(
        description=(
            "Component type. Use one of: gate_valve, globe_valve, ball_valve, "
            "butterfly_valve, check_valve, control_valve, relief_valve, rupture_disc, "
            "three_way_valve, orifice, strainer, sight_glass, reducer, spectacle_blind, "
            "sample_point, steam_trap, flame_arrestor."
        )
    )
    tag: str | None = Field(
        default=None,
        description="Tag for tagged items, e.g. 'FV-1201' for a control valve or 'PSV-1105' for a relief valve.",
    )
    fail_position: FailPosition | None = Field(
        default=None, description="Required for control valves: position on loss of signal or air."
    )
    set_pressure_psig: float | None = Field(default=None, description="Relief devices only: set pressure.")
    normally_closed: bool = Field(default=False, description="True if the valve is normally closed in operation.")
    notes: str = Field(default="", description="Sizing, locked-open status, etc.")


class Endpoint(BaseModel):
    """One end of a line: an equipment tag plus optionally the port it lands on."""

    equipment: str = Field(description="Tag of the equipment (or boundary connector) at this end.")
    port: str | None = Field(default=None, description="Port name on that equipment, if defined.")


class Stream(BaseModel):
    """A process or utility line."""

    number: str = Field(description='Full line number, e.g. \'6"-PL-1201-CS150\'.')
    source: Endpoint = Field(description="Where the line starts.")
    destination: Endpoint = Field(description="Where the line ends.")
    service: str = Field(description="Service code, e.g. 'PL', 'CW', 'ST'. See the service code list.")
    description: str = Field(default="", description="What flows, e.g. 'Degummed oil to bleacher'.")
    size_in: float | None = Field(default=None, description="Nominal size in inches.")
    spec: str = Field(default="", description="Piping spec code, e.g. 'CS150'.")
    phase: Phase | None = Field(default=None, description="Phase in normal operation.")
    inline: list[InlineItem] = Field(
        default_factory=list, description="In-line components in flow order from source to destination."
    )
    insulated: bool = Field(default=False, description="True if insulated.")
    traced: bool = Field(default=False, description="True if steam or electrically traced.")
    design_flow: str = Field(default="", description="Normal flow, e.g. '150 gpm'.")
    notes: str = Field(default="", description="Slope, no-pocket, free-drain requirements, etc.")

    @property
    def is_utility(self) -> bool:
        return self.service in {"CW", "CWR", "SW", "PW", "ST", "SC", "IA", "PA", "N2", "NG"}


class Instrument(BaseModel):
    """A tagged instrument or control-system function."""

    tag: str = Field(description="ISA-5.1 tag, e.g. 'FT-1201', 'LIC-1101', 'PSH-1105'.")
    description: str = Field(description="Service, e.g. 'Bleacher outlet oil temperature'.")
    attached_to: str = Field(
        description="Tag of the equipment or the line number this instrument measures or acts on."
    )
    location: str = Field(
        default="field",
        description=(
            "Readout location: field, field_aux, shared_display, shared_aux, computer, plc, inaccessible."
        ),
    )
    signal: str = Field(
        default="electric",
        description="Signal type: electric, pneumatic, data, capillary, hydraulic, software.",
    )
    loop: str | None = Field(default=None, description="Loop number this instrument belongs to, e.g. '1201'.")
    range_low: float | None = Field(default=None, description="Calibrated range low.")
    range_high: float | None = Field(default=None, description="Calibrated range high.")
    units: str = Field(default="", description="Engineering units, e.g. 'degF', 'psig', 'gpm', '%'.")
    setpoint: str = Field(default="", description="Normal setpoint or trip point, with units.")
    notes: str = Field(default="", description="Anything else, e.g. 'thermowell, 316L'.")


class ControlLoop(BaseModel):
    """A closed control loop: what it measures, what it moves, how it behaves."""

    number: str = Field(description="Loop number, e.g. '1201'.")
    description: str = Field(description="What the loop does, e.g. 'Bleacher outlet temperature control'.")
    controlled_variable: str = Field(description="What is held constant, e.g. 'Oil outlet temperature'.")
    measurement_tags: list[str] = Field(
        default_factory=list, description="Sensing instrument tags, e.g. ['TE-1201', 'TT-1201']."
    )
    controller_tag: str = Field(description="Controller tag, e.g. 'TIC-1201'.")
    final_element_tag: str = Field(description="Final control element tag, e.g. 'TV-1201'.")
    action: ControlAction | None = Field(default=None, description="Controller action.")
    setpoint: str = Field(default="", description="Normal setpoint with units.")
    cascade_from: str | None = Field(default=None, description="Master loop number, if this is a slave.")
    notes: str = Field(default="", description="Tuning notes, split-range details, etc.")


class Interlock(BaseModel):
    """A safety interlock or trip, stated as cause and effect."""

    tag: str = Field(description="Interlock tag, e.g. 'I-1201' or 'SIF-01'.")
    description: str = Field(description="What the interlock protects against.")
    initiators: list[str] = Field(
        default_factory=list, description="Instrument tags that trip it, e.g. ['LSL-1101']."
    )
    actions: list[str] = Field(
        default_factory=list,
        description="What happens, e.g. ['Stop P-1201A', 'Close FV-1201'].",
    )
    trip_setpoint: str = Field(default="", description="Trip point with units.")
    reset: str = Field(default="", description="'manual' or 'automatic'.")
    sil: str = Field(default="", description="SIL rating if the interlock is a safety instrumented function.")


class PIDModel(BaseModel):
    """A complete P&ID: metadata plus the equipment graph, instruments and logic."""

    project: str = Field(default="", description="Project or plant name.")
    drawing_number: str = Field(default="", description="Drawing number.")
    title: str = Field(description="Drawing title, e.g. 'Oil Bleaching — P&ID'.")
    revision: str = Field(default="A", description="Revision letter or number.")
    description: str = Field(default="", description="One-paragraph description of the process shown.")

    equipment: list[Equipment] = Field(default_factory=list)
    streams: list[Stream] = Field(default_factory=list)
    instruments: list[Instrument] = Field(default_factory=list)
    loops: list[ControlLoop] = Field(default_factory=list)
    interlocks: list[Interlock] = Field(default_factory=list)

    assumptions: list[str] = Field(
        default_factory=list, description="Assumptions made because the input did not say."
    )
    notes: list[str] = Field(default_factory=list, description="General drawing notes.")

    # -- lookups ------------------------------------------------------------

    def equipment_by_tag(self, tag: str) -> Equipment | None:
        return next((e for e in self.equipment if e.tag == tag), None)

    def stream_by_number(self, number: str) -> Stream | None:
        return next((s for s in self.streams if s.number == number), None)

    def instrument_by_tag(self, tag: str) -> Instrument | None:
        return next((i for i in self.instruments if i.tag == tag), None)

    def streams_from(self, tag: str) -> list[Stream]:
        return [s for s in self.streams if s.source.equipment == tag]

    def streams_to(self, tag: str) -> list[Stream]:
        return [s for s in self.streams if s.destination.equipment == tag]

    def instruments_on(self, tag: str) -> list[Instrument]:
        """Instruments attached to an equipment tag or a line number."""
        return [i for i in self.instruments if i.attached_to == tag]

    def inline_items(self) -> list[tuple[Stream, InlineItem]]:
        """Every in-line component, paired with the line it sits on."""
        return [(s, item) for s in self.streams for item in s.inline]

    def process_equipment(self) -> list[Equipment]:
        """Everything that is not an off-page connector."""
        return [e for e in self.equipment if not e.is_boundary]

    def all_tags(self) -> list[str]:
        """Every tag in the drawing, including duplicates so they can be counted."""
        tags = [e.tag for e in self.equipment]
        tags += [s.number for s in self.streams]
        tags += [i.tag for i in self.instruments]
        tags += [item.tag for _, item in self.inline_items() if item.tag]
        tags += [k.tag for k in self.interlocks]
        return tags
