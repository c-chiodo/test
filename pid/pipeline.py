"""Orchestration: run the agents in order and merge what they produce.

The pipeline is plain code, not an agent loop, because the order is known in
advance: you cannot instrument equipment that has not been laid out, and you
cannot safeguard a unit whose control valves do not exist yet.

    intake -> instrumentation -> safety -> validate -> review (repeat) -> validate

Two properties are worth calling out:

**Merges are additive and idempotent.** The instrumentation and safety agents
return additions, which are applied against existing tags. Anything that would
duplicate a tag or attach to a line that does not exist is skipped and reported
rather than silently dropped.

**Revisions must earn their place.** The review agent returns a whole redrawn
model. The pipeline re-validates it and keeps it only if it is measurably no
worse than what it replaced, and only if it has not quietly deleted equipment.
A confident-sounding bad revision therefore cannot reach the output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable

from .agents import (
    AgentSettings,
    InstrumentationAgent,
    IntakeAgent,
    ReviewAgent,
    SafetyAgent,
)
from .agents.results import InlineAddition, InstrumentationResult, SafetyResult
from .model import PIDModel
from .rules import ValidationReport, validate

Progress = Callable[[str], None]

#: Weights for comparing two drawings' findings. An error outranks any number
#: of notes, so a revision that trades one error for a handful of notes is an
#: improvement and one that does the reverse is not.
_SEVERITY_WEIGHTS = {"error": 100, "warning": 10, "info": 1}

#: Findings whose accepted fix may be to delete the subject entirely.
_REMOVAL_IS_A_VALID_FIX = {"R201"}


@dataclass
class StageResult:
    """What one pass of the pipeline did."""

    name: str
    summary: str
    accepted: bool = True
    skipped: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        status = "" if self.accepted else " (rejected)"
        line = f"{self.name}: {self.summary}{status}"
        for item in self.skipped:
            line += f"\n    skipped: {item}"
        return line


@dataclass
class BuildResult:
    model: PIDModel
    report: ValidationReport
    stages: list[StageResult] = field(default_factory=list)
    settings: AgentSettings | None = None

    def summary(self) -> str:
        lines = [str(stage) for stage in self.stages]
        lines.append(f"validation: {self.report.summary()}")
        if self.settings and self.settings.usage_log:
            inp, out = self.settings.tokens_used()
            lines.append(
                f"tokens: {inp:,} in / {out:,} out over {len(self.settings.usage_log)} calls"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Merging agent output into the drawing
# ---------------------------------------------------------------------------


def apply_inline_additions(
    model: PIDModel, additions: list[InlineAddition]
) -> list[str]:
    """Add in-line components to existing lines. Returns anything skipped."""
    skipped: list[str] = []
    for addition in additions:
        stream = model.stream_by_number(addition.line_number)
        if stream is None:
            skipped.append(
                f"{addition.item.type} for line {addition.line_number!r}, which does not exist"
            )
            continue
        if addition.item.tag and addition.item.tag in set(model.all_tags()):
            skipped.append(f"{addition.item.tag} — tag already used on the drawing")
            continue
        # An untagged component of the same type in the same place is almost
        # certainly the same component proposed twice.
        if addition.item.tag is None and any(
            existing.type == addition.item.type and existing.tag is None
            for existing in stream.inline
        ):
            skipped.append(
                f"second untagged {addition.item.type} on {stream.number} — treated as a duplicate"
            )
            continue
        if addition.insert_at is None or not 0 <= addition.insert_at <= len(stream.inline):
            stream.inline.append(addition.item)
        else:
            stream.inline.insert(addition.insert_at, addition.item)
    return skipped


def merge_instrumentation(model: PIDModel, result: InstrumentationResult) -> list[str]:
    """Merge instruments, loops and control valves into ``model``."""
    skipped = apply_inline_additions(model, result.inline_additions)

    existing_instruments = {i.tag for i in model.instruments}
    for instrument in result.instruments:
        if instrument.tag in existing_instruments:
            skipped.append(f"instrument {instrument.tag} — already on the drawing")
            continue
        model.instruments.append(instrument)
        existing_instruments.add(instrument.tag)

    existing_loops = {loop.number for loop in model.loops}
    for loop in result.loops:
        if loop.number in existing_loops:
            skipped.append(f"loop {loop.number} — already on the drawing")
            continue
        model.loops.append(loop)
        existing_loops.add(loop.number)

    model.assumptions.extend(result.assumptions)
    model.notes.extend(result.notes)
    return skipped


def merge_safety(model: PIDModel, result: SafetyResult) -> list[str]:
    """Merge relief devices, isolation, trips and their lines into ``model``."""
    skipped: list[str] = []

    # Equipment and streams first: relief lines need somewhere to discharge to
    # before the devices that sit on them can be attached.
    existing_equipment = {e.tag for e in model.equipment}
    for equipment in result.new_equipment:
        if equipment.tag in existing_equipment:
            skipped.append(f"equipment {equipment.tag} — already on the drawing")
            continue
        model.equipment.append(equipment)
        existing_equipment.add(equipment.tag)

    existing_streams = {s.number for s in model.streams}
    for stream in result.new_streams:
        if stream.number in existing_streams:
            skipped.append(f"line {stream.number} — already on the drawing")
            continue
        model.streams.append(stream)
        existing_streams.add(stream.number)

    skipped += apply_inline_additions(model, result.inline_additions)

    existing_instruments = {i.tag for i in model.instruments}
    for instrument in result.instruments:
        if instrument.tag in existing_instruments:
            skipped.append(f"instrument {instrument.tag} — already on the drawing")
            continue
        model.instruments.append(instrument)
        existing_instruments.add(instrument.tag)

    existing_interlocks = {k.tag for k in model.interlocks}
    for interlock in result.interlocks:
        if interlock.tag in existing_interlocks:
            skipped.append(f"interlock {interlock.tag} — already on the drawing")
            continue
        model.interlocks.append(interlock)
        existing_interlocks.add(interlock.tag)

    model.assumptions.extend(result.assumptions)
    model.notes.extend(result.notes)
    return skipped


# ---------------------------------------------------------------------------
# Accepting or rejecting a revision
# ---------------------------------------------------------------------------


def _weighted_score(report: ValidationReport) -> int:
    return sum(_SEVERITY_WEIGHTS[f.severity.value] for f in report.findings)


def judge_revision(
    before: PIDModel,
    before_report: ValidationReport,
    after: PIDModel,
    after_report: ValidationReport,
) -> tuple[bool, str]:
    """Decide whether a revision is an improvement.

    Returns ``(accept, reason)``. Deterministic on purpose: the review agent
    does not get to grade its own work.
    """
    if len(after_report.errors) > len(before_report.errors):
        return False, (
            f"errors rose from {len(before_report.errors)} to {len(after_report.errors)}"
        )

    # Deleting equipment is a legitimate fix only where a finding said so.
    permitted = {
        f.subject
        for f in before_report.findings
        if f.rule_id in _REMOVAL_IS_A_VALID_FIX
    }
    lost = {e.tag for e in before.equipment} - {e.tag for e in after.equipment} - permitted
    if lost:
        return False, f"dropped equipment that no finding asked to remove: {', '.join(sorted(lost))}"

    before_score, after_score = _weighted_score(before_report), _weighted_score(after_report)
    if after_score > before_score:
        return False, f"weighted findings worsened ({before_score} -> {after_score})"

    if after_score == before_score:
        return False, "no measurable improvement"

    return True, (
        f"{before_report.summary()} -> {after_report.summary()}"
    )


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def build_pid(
    description: str,
    *,
    settings: AgentSettings | None = None,
    client=None,
    area: int = 1000,
    equipment_hints: list[str] | None = None,
    control_philosophy: str = "",
    hazards: str = "",
    review_rounds: int = 2,
    skip_instrumentation: bool = False,
    skip_safety: bool = False,
    progress: Progress | None = None,
) -> BuildResult:
    """Build a validated P&ID from a process description.

    Args:
        description: What the process does, in prose.
        settings: Model configuration; defaults to Opus 5 at high effort.
        client: An Anthropic client, if you want to supply your own.
        area: Tag numbering block, e.g. ``1200``.
        equipment_hints: Equipment that must appear on the drawing.
        control_philosophy: Guidance for the instrumentation pass.
        hazards: Known hazards for the safety pass.
        review_rounds: How many times to attempt to close out findings.
        skip_instrumentation: Stop after the equipment graph.
        skip_safety: Skip the safeguarding pass.
        progress: Called with a one-line status before each pass.
    """
    settings = settings or AgentSettings()
    say = progress or (lambda _message: None)
    stages: list[StageResult] = []

    say("laying out equipment and piping")
    model = IntakeAgent(client, settings).run(
        description, equipment_hints=equipment_hints, area=area
    )
    stages.append(
        StageResult(
            "intake",
            f"{len(model.process_equipment())} equipment items, {len(model.streams)} lines",
        )
    )

    if not skip_instrumentation:
        say("adding instrumentation and control loops")
        result = InstrumentationAgent(client, settings).run(
            model, control_philosophy=control_philosophy
        )
        skipped = merge_instrumentation(model, result)
        stages.append(
            StageResult(
                "instrumentation",
                f"{len(result.instruments)} instruments, {len(result.loops)} loops, "
                f"{len(result.inline_additions)} in-line components",
                skipped=skipped,
            )
        )

    if not skip_safety:
        say("adding relief, isolation and trip logic")
        result = SafetyAgent(client, settings).run(model, hazards=hazards)
        skipped = merge_safety(model, result)
        stages.append(
            StageResult(
                "safety",
                f"{len(result.inline_additions)} in-line components, "
                f"{len(result.interlocks)} interlocks, {len(result.new_streams)} lines",
                skipped=skipped,
            )
        )

    report = validate(model)
    for attempt in range(1, max(0, review_rounds) + 1):
        if report.ok and not report.warnings:
            break
        say(f"review round {attempt}: closing out {report.summary()}")
        revised = ReviewAgent(client, settings).run(model, report)
        revised_report = validate(revised)
        accept, reason = judge_revision(model, report, revised, revised_report)
        stages.append(StageResult(f"review {attempt}", reason, accepted=accept))
        if not accept:
            break
        model, report = revised, revised_report

    return BuildResult(model=model, report=report, stages=stages, settings=settings)


def revise_pid(
    model: PIDModel,
    guidance: str,
    *,
    settings: AgentSettings | None = None,
    client=None,
    review_rounds: int = 1,
    progress: Progress | None = None,
) -> BuildResult:
    """Apply a change to an existing drawing, then re-validate and review it."""
    settings = settings or AgentSettings()
    say = progress or (lambda _message: None)
    stages: list[StageResult] = []

    say("revising the layout")
    revised = IntakeAgent(client, settings).refine_from_json(
        model.model_dump_json(), guidance
    )
    # The intake agent only restates equipment and piping, so carry the
    # instrumentation and safeguarding across, minus anything whose attachment
    # point no longer exists.
    valid_targets = {e.tag for e in revised.equipment} | {s.number for s in revised.streams}
    dropped: list[str] = []
    for instrument in model.instruments:
        if instrument.attached_to in valid_targets:
            revised.instruments.append(instrument)
        else:
            dropped.append(f"{instrument.tag} (was on {instrument.attached_to})")
    revised.loops = list(model.loops)
    revised.interlocks = list(model.interlocks)
    stages.append(
        StageResult(
            "revise",
            f"{len(revised.process_equipment())} equipment items, {len(revised.streams)} lines",
            skipped=dropped,
        )
    )

    model = revised
    report = validate(model)
    for attempt in range(1, max(0, review_rounds) + 1):
        if report.ok and not report.warnings:
            break
        say(f"review round {attempt}: closing out {report.summary()}")
        candidate = ReviewAgent(client, settings).run(model, report)
        candidate_report = validate(candidate)
        accept, reason = judge_revision(model, report, candidate, candidate_report)
        stages.append(StageResult(f"review {attempt}", reason, accepted=accept))
        if not accept:
            break
        model, report = candidate, candidate_report

    return BuildResult(model=model, report=report, stages=stages, settings=settings)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_model(model: PIDModel, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return target


def load_model(path: str | Path) -> PIDModel:
    raw = Path(path).read_text(encoding="utf-8")
    return PIDModel.model_validate(json.loads(raw))
