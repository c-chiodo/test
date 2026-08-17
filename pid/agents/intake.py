"""Intake agent: process description in, equipment graph out."""

from __future__ import annotations

import json

from ..model import PIDModel
from .base import Agent
from .results import IntakeResult

INSTRUCTIONS = """\
You are the process engineer who lays out the drawing. From a description of a \
process, produce the equipment list and the piping that connects it. Another \
agent adds instrumentation afterwards, and a third adds safeguarding — leave \
both to them.

What to produce:

* One equipment item per physical item of plant named or implied by the \
process. An installed spare is its own item (P-1201A and P-1201B), linked with \
`spared_by`.
* Nozzles on each item, named plainly ("suction", "discharge", "N1", "vapour"), \
enough for every line you draw to land on a real connection.
* Every process line, in flow order through the unit, plus the utility lines \
the process needs to work: steam or hot oil to heaters, cooling water to \
coolers, nitrogen to blanketed tanks, instrument air where it is called for.
* A boundary connector at each point where material enters or leaves the \
drawing, so no line dangles.
* Manual block valves where an operator needs them: either side of equipment \
that gets maintained, and on tie-ins.

What to leave out: instruments, control valves, control loops, relief devices \
and interlocks. Plain manual valves and fittings are yours; anything that \
measures or trips is not.

Scope the drawing the way a real P&ID is scoped — one process unit per sheet. \
If the description covers several units, draw the one it emphasises and note \
the others as boundary connectors.\
"""


class IntakeAgent(Agent):
    """Turns a natural-language process description into an equipment graph."""

    name = "intake"
    instructions = INSTRUCTIONS

    def run(
        self,
        description: str,
        *,
        equipment_hints: list[str] | None = None,
        area: int = 1000,
    ) -> PIDModel:
        """Build the initial model.

        Args:
            description: Free-text description of the process.
            equipment_hints: Known equipment tags or names to include verbatim.
            area: Numbering block for tags, e.g. ``1200`` for 1200-series tags.
        """
        prompt = [
            "Lay out the P&ID for this process.",
            "",
            "## Process description",
            description.strip(),
            "",
            f"## Tag numbering\nUse the {area}-series numbering block for equipment, "
            f"lines and loops on this drawing.",
        ]
        if equipment_hints:
            prompt += [
                "",
                "## Equipment that must appear",
                *(f"- {hint}" for hint in equipment_hints),
            ]

        result = self.ask("\n".join(prompt), IntakeResult)
        return PIDModel(
            project=result.project,
            drawing_number=result.drawing_number,
            title=result.title,
            description=result.description,
            equipment=result.equipment,
            streams=result.streams,
            assumptions=list(result.assumptions),
            notes=list(result.notes),
        )

    def refine_from_json(self, model_json: str, guidance: str) -> PIDModel:
        """Re-lay-out an existing drawing under new guidance.

        Used by ``pid revise`` when the change is structural — adding a unit,
        re-routing a header — rather than a matter of instrumentation.
        """
        prompt = [
            "Revise this existing P&ID layout as instructed. Keep every tag that "
            "is still valid so the revision reads as a change, not a redraw.",
            "",
            "## Instruction",
            guidance.strip(),
            "",
            "## Existing drawing",
            "```json",
            json.dumps(json.loads(model_json), indent=2),
            "```",
        ]
        result = self.ask("\n".join(prompt), IntakeResult)
        return PIDModel(
            project=result.project,
            drawing_number=result.drawing_number,
            title=result.title,
            description=result.description,
            equipment=result.equipment,
            streams=result.streams,
            assumptions=list(result.assumptions),
            notes=list(result.notes),
        )
