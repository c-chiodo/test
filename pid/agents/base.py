"""Shared plumbing for the P&ID agents.

Every agent is the same shape: a system prompt encoding one engineering
discipline, plus a Pydantic output schema. The Claude API's structured-output
support does the rest — the model is forced to return JSON matching the schema,
so the pipeline never parses free text.

Two deliberate choices here:

* **Structured output, not tool loops.** Each agent does one pass and returns
  data. There is nothing for it to iterate on, so an agentic loop would only add
  latency and non-determinism.
* **The prompt carries conventions, the code carries checks.** Prompts explain
  ISA-5.1 and good practice so the agents produce sensible drawings;
  :mod:`pid.rules` then decides independently whether they did. Neither trusts
  the other.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

#: Anthropic's most capable widely available coding/agentic model. Engineering
#: judgement is exactly the kind of work that rewards the top tier here.
DEFAULT_MODEL = "claude-opus-5"

#: Opus 5's safety classifiers can decline a request outright. Opting into
#: server-side fallbacks means a decline is retried on another model inside the
#: same call rather than surfacing as a dead end.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

OutputT = TypeVar("OutputT", bound=BaseModel)


class AgentError(RuntimeError):
    """Raised when an agent cannot produce a usable result."""


@dataclass
class AgentSettings:
    """Per-run model configuration, shared by every agent in a pipeline."""

    model: str = DEFAULT_MODEL
    #: low | medium | high | xhigh | max. `high` is the API default and a good
    #: balance here; drop to `medium` for routine single-unit drawings.
    effort: str = "high"
    max_tokens: int = 16000
    #: Retry a safety-classifier decline on a fallback model server-side.
    refusal_fallback: bool = True
    #: Extra guidance appended to every agent's system prompt, e.g. a client's
    #: house tagging convention.
    house_rules: str = ""
    #: Records one line per API call: (agent, input_tokens, output_tokens).
    usage_log: list[tuple[str, int, int]] = field(default_factory=list)

    def tokens_used(self) -> tuple[int, int]:
        return (
            sum(entry[1] for entry in self.usage_log),
            sum(entry[2] for entry in self.usage_log),
        )


def build_client(api_key: str | None = None):
    """Construct an Anthropic client.

    Imported lazily so the rest of the package — validation, rendering,
    exports — works with the SDK absent and no credentials configured.
    """
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise AgentError(
            "the `anthropic` package is required to run the agents; "
            "install it with `pip install anthropic`"
        ) from exc

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    # A bare constructor still resolves an `ant auth login` profile, so an
    # unset ANTHROPIC_API_KEY is not by itself an error.
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


# ---------------------------------------------------------------------------
# The conventions block, shared by every agent
# ---------------------------------------------------------------------------

CONVENTIONS = """\
You are part of a team producing a piping and instrumentation diagram (P&ID) \
for a process plant. Your output is consumed by code, not by a person, and is \
checked afterwards by a deterministic rule engine.

Tagging conventions to follow exactly:

* Instruments use ISA-5.1 tags: a first letter for the measured variable \
(F flow, L level, P pressure, T temperature, A analysis, D density, S speed, \
W weight, Z position, M moisture, H hand), then function letters (E primary \
element, T transmit, I indicate, C control, R record, A alarm, S switch, \
V valve, G gauge/glass, Q totalise, Y compute), then a dash and the loop \
number: FE-1201, FT-1201, FIC-1201, FV-1201 are one flow loop. High and low \
modifiers come last: PSH-1105, LSLL-1101.
* Equipment prefixes: P pump, TK tank, V vessel, R reactor, C column, \
E heat exchanger, F filter, K compressor, B blower, CF centrifuge, DR dryer, \
M mixer, CV conveyor, WT scale, S separator.
* Line numbers are SIZE"-SERVICE-SEQUENCE-SPEC, e.g. 6"-PL-1201-CS150. \
Services include PL process liquid, PG process gas, PS slurry, OIL oil, \
FA fatty acid, CW/CWR cooling water, ST steam, SC condensate, IA instrument \
air, N2 nitrogen, CA caustic, AC acid, VE vent, DR drain. Specs include \
CS150, CS300, SS150, SS300, CS150J (steam jacketed), HDPE.
* Number tags by unit area: everything in one unit shares a numbering block \
(1100s, 1200s, ...) so a reviewer can tell at a glance where a tag belongs.
* Anything arriving from or leaving to another drawing is an equipment item \
with kind "boundary" — that is how off-page connectors are represented. Give \
each one a descriptive name like "From tank farm" or "To fatty acid storage".

Engineering conventions:

* Every line has both ends connected to an equipment item or a boundary \
connector. Nothing dangles.
* In-line components on a line are listed in flow order from source to \
destination.
* Give equipment its design pressure and design temperature. Where the input \
does not state them, choose values consistent with the service and record the \
choice in `assumptions`.

Deliver exactly the scope you are asked for at the level of detail asked for. \
Where the input is silent on something you must decide, make the call a \
competent process engineer would make and record it in `assumptions` rather \
than inventing requirements or asking a question. Do not add unrelated \
equipment, and do not omit equipment the input names.\
"""


class Agent:
    """Base class: a system prompt plus a structured-output call."""

    name: str = "agent"
    #: Discipline-specific instructions, appended to the shared conventions.
    instructions: str = ""

    def __init__(self, client=None, settings: AgentSettings | None = None) -> None:
        self.settings = settings or AgentSettings()
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = build_client()
        return self._client

    def system_prompt(self) -> list[dict]:
        """System prompt as cacheable blocks.

        The conventions block is byte-identical across agents and runs, so it
        is marked for caching; the per-agent instructions follow it.
        """
        blocks: list[dict] = [
            {"type": "text", "text": CONVENTIONS, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": self.instructions},
        ]
        if self.settings.house_rules:
            blocks.append(
                {"type": "text", "text": f"Project-specific rules:\n{self.settings.house_rules}"}
            )
        return blocks

    def ask(self, user_content: str, output_format: type[OutputT]) -> OutputT:
        """One structured-output call. Returns a validated instance of the schema."""
        settings = self.settings
        kwargs: dict = {
            "model": settings.model,
            "max_tokens": settings.max_tokens,
            "system": self.system_prompt(),
            "messages": [{"role": "user", "content": user_content}],
            "output_format": output_format,
            "output_config": {"effort": settings.effort},
        }

        if settings.refusal_fallback:
            response = self.client.beta.messages.parse(
                betas=[FALLBACK_BETA], fallbacks="default", **kwargs
            )
        else:
            response = self.client.messages.parse(**kwargs)

        usage = getattr(response, "usage", None)
        if usage is not None:
            settings.usage_log.append(
                (self.name, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0)
            )

        # Check why generation stopped before trusting the content.
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise AgentError(
                f"{self.name}: the request was declined by safety classifiers"
                + (f" (category: {category})" if category else "")
                + ". Rephrase the process description, or run with "
                "refusal_fallback enabled on a model that accepts it."
            )
        if stop_reason == "max_tokens":
            raise AgentError(
                f"{self.name}: output hit the {settings.max_tokens}-token limit and was "
                "truncated. Raise max_tokens, or split the process into fewer units per "
                "drawing — a P&ID covering one unit is easier to review anyway."
            )

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise AgentError(f"{self.name}: the model returned no parseable structured output")
        return parsed
