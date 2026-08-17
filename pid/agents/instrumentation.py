"""Instrumentation agent: adds measurement and control to an equipment graph."""

from __future__ import annotations

from ..model import PIDModel
from .base import Agent
from .results import InstrumentationResult

INSTRUCTIONS = """\
You are the control systems engineer. Given an equipment graph that is already \
piped, add the measurement and control needed to run the process.

For each control loop, produce the whole loop and nothing missing: the primary \
element, the transmitter, the controller, and the final control element, all \
sharing one loop number. The final control element is a control valve added to \
an existing line via `inline_additions` — give it the matching tag (loop 1201 \
gets FV-1201) and a fail position chosen from the process consequence of losing \
air, not from habit. A valve on a fuel or steam line normally fails closed; a \
cooling water valve to a hot service normally fails open.

Also add the indication operators need even where nothing is controlled: level \
on anything holding inventory, temperature across heat exchangers, pressure on \
pump discharges and on any vessel that can be pressured, flow where a rate must \
be known.

Choose readout locations honestly. `field` for a local gauge, `shared_display` \
for anything on the DCS, `plc` for logic in a PLC. Give transmitters a \
calibrated range and units; give controllers a setpoint.

Set `attached_to` to the equipment tag for anything measuring the equipment \
itself, or to the full line number for anything measuring the stream.

Leave relief devices, trip switches and interlocks alone — the safety agent \
adds those. A control loop that holds a variable at setpoint is yours; a switch \
that shuts the plant down is not.

Add the instrumentation the process needs, not every instrument that could be \
justified. An over-instrumented P&ID costs money and gets value-engineered \
back down.\
"""


class InstrumentationAgent(Agent):
    """Adds instruments, control loops and control valves."""

    name = "instrumentation"
    instructions = INSTRUCTIONS

    def run(self, model: PIDModel, *, control_philosophy: str = "") -> InstrumentationResult:
        """Propose instrumentation for ``model``.

        Args:
            model: The drawing so far, from the intake agent.
            control_philosophy: Optional guidance, e.g. "cascade the bleacher
                temperature off the heater outlet" or "all valves fail closed".
        """
        prompt = [
            "Add instrumentation and control to this drawing.",
            "",
            "## Drawing",
            "```json",
            _context_json(model),
            "```",
        ]
        if control_philosophy:
            prompt += ["", "## Control philosophy", control_philosophy.strip()]
        return self.ask("\n".join(prompt), InstrumentationResult)


def _context_json(model: PIDModel) -> str:
    """The drawing as the instrumentation agent needs to see it.

    Instruments, loops and interlocks are dropped: this agent is adding them,
    and showing it an empty list of its own output invites it to echo rather
    than think. Everything it needs to attach to — tags, ports, line numbers —
    stays.
    """
    return model.model_dump_json(
        indent=2,
        exclude={"instruments", "loops", "interlocks", "notes", "assumptions"},
        exclude_defaults=False,
    )
