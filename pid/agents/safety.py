"""Safety agent: overpressure protection, isolation and trip logic."""

from __future__ import annotations

from ..model import PIDModel
from .base import Agent
from .results import SafetyResult

INSTRUCTIONS = """\
You are the process safety engineer reviewing a drawing that is piped and \
instrumented. Add the safeguarding it is missing.

Overpressure protection. Every vessel, reactor, column, separator and heat \
exchanger that can be blocked in needs a relief device with a set pressure at \
or below the design pressure of the equipment it protects — never above it. \
Show where the relieving fluid goes: add the relief line and, if the drawing \
has nowhere to put it, a boundary connector such as "To vent header" or \
"To flare". A relief valve that discharges to nothing on the drawing cannot be \
reviewed. Where a heat exchanger's cold side can be blocked in against a hot \
utility, that is a relief case; say so in the notes.

Isolation and non-return. Rotating equipment needs a block valve either side so \
it can be worked on, and centrifugal pumps, compressors and blowers need a \
check valve on the discharge so a trip cannot back-flow the line through the \
machine. Add only what is missing — the drawing already has some manual valves.

Trip logic. Add the interlocks the process actually needs, each stated as cause \
and effect: the initiating instruments, the trip setpoint, and what happens. \
The usual cases are low level tripping a pump to protect it from running dry, \
high level stopping a feed, high temperature cutting heat input, and high \
pressure isolating a supply. Add the initiating switch as an instrument too \
(LSLL, PSH, TSH) with its setpoint — an interlock whose initiator is not on the \
drawing is not implemented.

Tag relief devices PSV-nnnn (or RD-nnnn for a rupture disc) and give them a set \
pressure. Add trip switches as instruments with `location` set to `plc` if the \
logic lives in a safety PLC.

Safeguard against the credible cases, not every conceivable one. A P&ID covered \
in interlocks nobody can justify gets ignored in the HAZOP, which is worse than \
one that shows the few that matter.\
"""


class SafetyAgent(Agent):
    """Adds relief devices, isolation, trip switches and interlocks."""

    name = "safety"
    instructions = INSTRUCTIONS

    def run(self, model: PIDModel, *, hazards: str = "") -> SafetyResult:
        """Propose safeguarding for ``model``.

        Args:
            model: The drawing after instrumentation.
            hazards: Optional guidance, e.g. "the oil is above its flash point
                at the heater outlet" or "the caustic line must be double
                blocked".
        """
        prompt = [
            "Add overpressure protection, isolation and trip logic to this drawing.",
            "",
            "## Drawing",
            "```json",
            model.model_dump_json(indent=2, exclude={"interlocks"}),
            "```",
        ]
        if hazards:
            prompt += ["", "## Known hazards and constraints", hazards.strip()]
        return self.ask("\n".join(prompt), SafetyResult)
