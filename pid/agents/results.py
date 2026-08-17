"""Output schemas for the agents.

Each agent returns one of these; :mod:`pid.pipeline` merges them into the
drawing. They are separate from :mod:`pid.model` because they describe *edits*
to a P&ID rather than the P&ID itself — an agent that adds a check valve should
say "add this to that line", not hand back a whole redrawn sheet.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..model import ControlLoop, Equipment, InlineItem, Instrument, Interlock, Stream


class InlineAddition(BaseModel):
    """Add an in-line component to a line that already exists."""

    line_number: str = Field(description="Line number to add the component to, exactly as tagged.")
    item: InlineItem = Field(description="The component to add.")
    insert_at: int | None = Field(
        default=None,
        description=(
            "Zero-based position in the line's existing flow-order list. "
            "Omit to append at the downstream end."
        ),
    )
    reason: str = Field(default="", description="Why this component is needed.")


class IntakeResult(BaseModel):
    """The equipment graph: what is on the drawing and how it is piped."""

    title: str = Field(description="Drawing title, e.g. 'Oil Bleaching — P&ID'.")
    project: str = Field(default="", description="Plant or project name if the input gives one.")
    drawing_number: str = Field(default="", description="Drawing number if the input gives one.")
    description: str = Field(description="One paragraph describing the process shown.")
    equipment: list[Equipment] = Field(
        description="Every equipment item and boundary connector on the drawing."
    )
    streams: list[Stream] = Field(
        description="Every process and utility line, each connecting two equipment items."
    )
    assumptions: list[str] = Field(
        default_factory=list, description="Design decisions made because the input was silent."
    )
    notes: list[str] = Field(default_factory=list, description="General drawing notes.")


class InstrumentationResult(BaseModel):
    """Measurement and control added to an existing equipment graph."""

    instruments: list[Instrument] = Field(description="Instruments to add.")
    loops: list[ControlLoop] = Field(description="Control loops to add.")
    inline_additions: list[InlineAddition] = Field(
        default_factory=list,
        description="Control valves and flow elements to add to existing lines.",
    )
    assumptions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list, description="Notes about the control philosophy."
    )


class SafetyResult(BaseModel):
    """Overpressure protection, isolation and trip logic."""

    inline_additions: list[InlineAddition] = Field(
        default_factory=list,
        description="Relief devices, block valves and check valves to add to existing lines.",
    )
    new_equipment: list[Equipment] = Field(
        default_factory=list,
        description=(
            "Boundary connectors needed to route relief and vent discharges, "
            "e.g. a 'To vent header' connector. Use kind 'boundary'."
        ),
    )
    new_streams: list[Stream] = Field(
        default_factory=list,
        description="Relief, vent and drain lines that do not exist yet.",
    )
    instruments: list[Instrument] = Field(
        default_factory=list,
        description="Safety instruments: trip switches, high/low alarms, relief device tags.",
    )
    interlocks: list[Interlock] = Field(
        default_factory=list, description="Interlocks and trips, as cause and effect."
    )
    assumptions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list, description="Notes on the safeguarding basis."
    )
