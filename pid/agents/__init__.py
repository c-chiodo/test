"""The agents that build a P&ID.

Four passes, each a single structured-output call to Claude, each with one job:

===================================  =======================================
:class:`~pid.agents.intake.IntakeAgent`                  process description -> equipment and piping
:class:`~pid.agents.instrumentation.InstrumentationAgent`  measurement and control loops
:class:`~pid.agents.safety.SafetyAgent`                    relief, isolation and trip logic
:class:`~pid.agents.review.ReviewAgent`                    closes out validation findings
===================================  =======================================

:mod:`pid.pipeline` runs them in order, validating between passes.
"""

from .base import DEFAULT_MODEL, Agent, AgentError, AgentSettings, build_client
from .instrumentation import InstrumentationAgent
from .intake import IntakeAgent
from .results import InlineAddition, InstrumentationResult, IntakeResult, SafetyResult
from .review import ReviewAgent
from .safety import SafetyAgent

__all__ = [
    "DEFAULT_MODEL",
    "Agent",
    "AgentError",
    "AgentSettings",
    "build_client",
    "IntakeAgent",
    "InstrumentationAgent",
    "SafetyAgent",
    "ReviewAgent",
    "IntakeResult",
    "InstrumentationResult",
    "SafetyResult",
    "InlineAddition",
]
