"""Review agent: closes out findings raised by the rule engine.

This is the only agent that returns a whole corrected drawing rather than a set
of additions. Findings cut across the model — fixing a relief valve set above
design pressure might mean changing the valve, the vessel, or both — and a patch
protocol expressive enough to cover that is a worse bet than letting the agent
restate the drawing.

The risk of a full restatement is that the agent quietly drops content. The
pipeline guards against that deterministically: a revision is only accepted if
re-validation shows the error count did not go up, so a bad revision is
discarded rather than shipped.
"""

from __future__ import annotations

from ..model import PIDModel
from ..rules import ValidationReport
from .base import Agent

INSTRUCTIONS = """\
You are the checking engineer closing out review comments on a P&ID.

You are given the drawing and a list of findings from an automated check. \
Return the corrected drawing in full.

Fix the findings. Errors are things a reviewer would reject the drawing over; \
warnings are very likely wrong; notes are worth considering. Work down in that \
order and fix as many as you can justify.

Where a finding is wrong — the check has misread a legitimate design — leave the \
drawing as it is and add a line to `notes` saying why, referencing the rule ID. \
A false finding explained on the drawing is a better outcome than a real design \
changed to satisfy a checker.

Change only what the findings call for. Every tag, line number, loop and \
interlock not implicated by a finding must come back exactly as it was — this \
output is diffed against the previous revision, and gratuitous renumbering \
makes that diff unreadable. Do not drop anything: an item you omit is an item \
deleted from the drawing.\
"""


class ReviewAgent(Agent):
    """Applies fixes for validation findings and returns the corrected drawing."""

    name = "review"
    instructions = INSTRUCTIONS

    def run(self, model: PIDModel, report: ValidationReport) -> PIDModel:
        """Return a revised drawing addressing ``report``."""
        prompt = [
            "Close out these review findings and return the corrected drawing.",
            "",
            "## Findings",
            _format_findings(report),
            "",
            "## Drawing",
            "```json",
            model.model_dump_json(indent=2),
            "```",
        ]
        return self.ask("\n".join(prompt), PIDModel)


def _format_findings(report: ValidationReport) -> str:
    """Findings as a compact list, most severe first."""
    if not report.findings:
        return "No findings."
    order = {"error": 0, "warning": 1, "info": 2}
    ranked = sorted(
        report.findings, key=lambda f: (order[f.severity.value], f.rule_id, f.subject)
    )
    lines = []
    for finding in ranked:
        line = f"- [{finding.severity.value}] {finding.rule_id} {finding.subject}: {finding.message}"
        if finding.suggestion:
            line += f" ({finding.suggestion})"
        lines.append(line)
    return "\n".join(lines)
