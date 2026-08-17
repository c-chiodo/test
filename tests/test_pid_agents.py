"""The agent layer, driven by a stub client so nothing touches the network.

What is worth testing here is the wiring, not the model's judgement: that each
agent asks for the right schema, that the conventions block stays cacheable and
identical across agents, and that a refusal or a truncated response is reported
as an actionable error rather than surfacing as a confusing parse failure.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel

from pid.agents import (
    AgentError,
    AgentSettings,
    InstrumentationAgent,
    IntakeAgent,
    ReviewAgent,
    SafetyAgent,
)
from pid.agents.base import CONVENTIONS, FALLBACK_BETA, Agent
from pid.agents.results import InstrumentationResult, IntakeResult, SafetyResult
from pid.examples import bleaching_unit
from pid.model import Equipment, PIDModel
from pid.rules import validate


# -- a stub Anthropic client -----------------------------------------------


@dataclass
class _Usage:
    input_tokens: int = 1000
    output_tokens: int = 500


@dataclass
class _Response:
    parsed_output: Any = None
    stop_reason: str = "end_turn"
    stop_details: Any = None
    usage: _Usage = field(default_factory=_Usage)


class _Messages:
    def __init__(self, owner: "StubClient") -> None:
        self._owner = owner

    def parse(self, **kwargs):
        self._owner.calls.append(kwargs)
        return self._owner.response


class _Beta:
    def __init__(self, owner: "StubClient") -> None:
        self.messages = _Messages(owner)


class StubClient:
    """Stands in for ``anthropic.Anthropic``, recording what it was asked."""

    def __init__(self, response: _Response | None = None) -> None:
        self.calls: list[dict] = []
        self.response = response or _Response()
        self.messages = _Messages(self)
        self.beta = _Beta(self)

    @property
    def last(self) -> dict:
        return self.calls[-1]


class _Echo(BaseModel):
    value: str


class _EchoAgent(Agent):
    name = "echo"
    instructions = "Echo the input."


# -- request construction --------------------------------------------------


def test_the_request_carries_model_effort_and_schema():
    client = StubClient(_Response(parsed_output=_Echo(value="ok")))
    settings = AgentSettings(model="claude-opus-5", effort="medium", max_tokens=4096)
    result = _EchoAgent(client, settings).ask("say ok", _Echo)

    assert result.value == "ok"
    call = client.last
    assert call["model"] == "claude-opus-5"
    assert call["max_tokens"] == 4096
    assert call["output_config"] == {"effort": "medium"}
    assert call["output_format"] is _Echo
    assert call["messages"] == [{"role": "user", "content": "say ok"}]


def test_refusal_fallback_is_on_by_default_and_uses_the_beta_endpoint():
    client = StubClient(_Response(parsed_output=_Echo(value="ok")))
    _EchoAgent(client, AgentSettings()).ask("hello", _Echo)
    assert client.last["fallbacks"] == "default"
    assert client.last["betas"] == [FALLBACK_BETA]


def test_refusal_fallback_can_be_turned_off():
    client = StubClient(_Response(parsed_output=_Echo(value="ok")))
    _EchoAgent(client, AgentSettings(refusal_fallback=False)).ask("hello", _Echo)
    assert "fallbacks" not in client.last
    assert "betas" not in client.last


def test_the_conventions_block_is_marked_for_caching_and_comes_first():
    client = StubClient(_Response(parsed_output=_Echo(value="ok")))
    _EchoAgent(client, AgentSettings()).ask("hello", _Echo)
    system = client.last["system"]
    assert system[0]["text"] == CONVENTIONS
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[1]["text"] == "Echo the input."


def test_every_agent_shares_the_same_cacheable_prefix():
    # The conventions block is the cache prefix, so it must be byte-identical
    # across agents or every pass pays a fresh cache write.
    prefixes = {
        agent(None, AgentSettings()).system_prompt()[0]["text"]
        for agent in (IntakeAgent, InstrumentationAgent, SafetyAgent, ReviewAgent)
    }
    assert len(prefixes) == 1


def test_house_rules_are_appended_to_every_agent():
    client = StubClient(_Response(parsed_output=_Echo(value="ok")))
    settings = AgentSettings(house_rules="Tag all pumps PU- not P-.")
    _EchoAgent(client, settings).ask("hello", _Echo)
    system = client.last["system"]
    assert "Tag all pumps PU- not P-." in system[-1]["text"]


def test_token_usage_is_accumulated_across_calls():
    client = StubClient(_Response(parsed_output=_Echo(value="ok")))
    settings = AgentSettings()
    agent = _EchoAgent(client, settings)
    agent.ask("one", _Echo)
    agent.ask("two", _Echo)
    assert settings.tokens_used() == (2000, 1000)
    assert [entry[0] for entry in settings.usage_log] == ["echo", "echo"]


# -- failure handling ------------------------------------------------------


def test_a_refusal_is_reported_as_an_actionable_error():
    @dataclass
    class _Details:
        category: str = "cyber"

    client = StubClient(_Response(stop_reason="refusal", stop_details=_Details()))
    with pytest.raises(AgentError) as excinfo:
        _EchoAgent(client, AgentSettings()).ask("hello", _Echo)
    assert "declined" in str(excinfo.value)
    assert "cyber" in str(excinfo.value)


def test_a_refusal_with_no_category_still_raises_cleanly():
    client = StubClient(_Response(stop_reason="refusal", stop_details=None))
    with pytest.raises(AgentError, match="declined"):
        _EchoAgent(client, AgentSettings()).ask("hello", _Echo)


def test_a_truncated_response_says_what_to_do_about_it():
    client = StubClient(_Response(stop_reason="max_tokens"))
    with pytest.raises(AgentError) as excinfo:
        _EchoAgent(client, AgentSettings(max_tokens=4096)).ask("hello", _Echo)
    message = str(excinfo.value)
    assert "truncated" in message
    assert "4096" in message
    assert "max_tokens" in message


def test_a_response_with_no_structured_output_raises():
    client = StubClient(_Response(parsed_output=None))
    with pytest.raises(AgentError, match="no parseable structured output"):
        _EchoAgent(client, AgentSettings()).ask("hello", _Echo)


def test_stop_reason_is_checked_before_the_content_is_trusted():
    # A refusal that also happens to carry parsed output must still raise: the
    # content of a declined response is not the answer to the question asked.
    client = StubClient(_Response(parsed_output=_Echo(value="junk"), stop_reason="refusal"))
    with pytest.raises(AgentError):
        _EchoAgent(client, AgentSettings()).ask("hello", _Echo)


# -- per-agent wiring ------------------------------------------------------


def test_intake_asks_for_the_equipment_graph_and_builds_a_model():
    payload = IntakeResult(
        title="Tiny unit",
        project="Test plant",
        drawing_number="PID-001",
        description="A tank",
        equipment=[Equipment(tag="TK-1101", name="Tank", kind="tank")],
        streams=[],
        assumptions=["assumed a tank"],
        notes=["a note"],
    )
    client = StubClient(_Response(parsed_output=payload))
    model = IntakeAgent(client, AgentSettings()).run("There is a tank.", area=1100)

    assert client.last["output_format"] is IntakeResult
    assert "1100-series" in client.last["messages"][0]["content"]
    assert "There is a tank." in client.last["messages"][0]["content"]
    assert isinstance(model, PIDModel)
    assert model.title == "Tiny unit"
    assert model.equipment_by_tag("TK-1101") is not None
    assert model.assumptions == ["assumed a tank"] and model.notes == ["a note"]


def test_intake_passes_required_equipment_through():
    client = StubClient(
        _Response(parsed_output=IntakeResult(title="t", description="d", equipment=[], streams=[]))
    )
    IntakeAgent(client, AgentSettings()).run(
        "a process", equipment_hints=["P-1201A existing feed pump"]
    )
    assert "P-1201A existing feed pump" in client.last["messages"][0]["content"]


def test_instrumentation_is_not_shown_the_instruments_it_is_meant_to_add():
    # Echoing an empty instrument list back at the agent invites it to fill in
    # the shape rather than think about the process.
    client = StubClient(
        _Response(parsed_output=InstrumentationResult(instruments=[], loops=[]))
    )
    InstrumentationAgent(client, AgentSettings()).run(
        bleaching_unit(), control_philosophy="all valves fail closed"
    )
    prompt = client.last["messages"][0]["content"]
    assert client.last["output_format"] is InstrumentationResult
    assert "all valves fail closed" in prompt
    assert "TK-1201" in prompt  # it still sees what to attach to
    # Line numbers reach the prompt JSON-escaped (4\"-OIL-1204-CS150), which is
    # what the model reads back when it sets `attached_to`.
    assert "OIL-1204-CS150" in prompt
    assert "FIC-1201" not in prompt  # but not the existing instrumentation
    assert "instruments" not in prompt


def test_safety_sees_the_instrumented_drawing_and_the_stated_hazards():
    client = StubClient(_Response(parsed_output=SafetyResult()))
    SafetyAgent(client, AgentSettings()).run(
        bleaching_unit(), hazards="oil is above its flash point"
    )
    prompt = client.last["messages"][0]["content"]
    assert client.last["output_format"] is SafetyResult
    assert "oil is above its flash point" in prompt
    assert "FIC-1201" in prompt  # safety needs to see the control valves
    assert "I-1201" not in prompt  # but not the interlocks it is adding


def test_review_is_given_the_findings_most_severe_first():
    model = bleaching_unit()
    model.instrument_by_tag("PI-1207").attached_to = "nonsense"  # error
    model.streams[0].phase = None  # note
    report = validate(model)

    client = StubClient(_Response(parsed_output=bleaching_unit()))
    revised = ReviewAgent(client, AgentSettings()).run(model, report)

    prompt = client.last["messages"][0]["content"]
    assert client.last["output_format"] is PIDModel
    assert prompt.index("[error]") < prompt.index("[info]")
    assert "R004" in prompt
    assert isinstance(revised, PIDModel)


def test_review_of_a_clean_drawing_says_so():
    from pid.agents.review import _format_findings
    from pid.rules import ValidationReport

    assert _format_findings(ValidationReport()) == "No findings."


# -- client construction ---------------------------------------------------


def test_a_client_is_only_built_when_an_agent_actually_calls_out():
    # Constructing an agent must not require credentials; the rest of the
    # package has to work with the SDK absent.
    agent = IntakeAgent(None, AgentSettings())
    assert agent._client is None


def test_build_client_reports_a_missing_sdk_clearly(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("no module named anthropic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from pid.agents.base import build_client

    with pytest.raises(AgentError, match="pip install anthropic"):
        build_client()
