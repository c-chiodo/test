"""Agentic P&ID builder.

Describe a process in prose; get back a piping and instrumentation diagram, the
schedules that go with it, and a list of everything the drawing still gets wrong.

Four agents build the drawing — layout, instrumentation, safeguarding, then a
review pass — and a deterministic rule engine checks the result. The agents
propose; the rule engine, the renderer and the exporters are ordinary code, so
everything after the last API call is reproducible and free to re-run.

    from pid import build_pid, render_svg, validate

    result = build_pid("Crude soybean oil is pumped from a day tank ...", area=1200)
    print(result.report.to_text())
    open("bleaching.svg", "w").write(render_svg(result.model))

Command line equivalent::

    pid build "Crude soybean oil is pumped ..." --area 1200 --out drawings/bleaching
"""

from .exports import (
    equipment_list_csv,
    instrument_index_csv,
    interlock_schedule_csv,
    line_list_csv,
    loop_schedule_csv,
    relief_schedule_csv,
    write_package,
)
from .layout import compute_layout
from .model import (
    ControlLoop,
    Endpoint,
    Equipment,
    InlineItem,
    Instrument,
    Interlock,
    PIDModel,
    Port,
    Stream,
)
from .pipeline import BuildResult, build_pid, load_model, revise_pid, save_model
from .render import render_svg
from .rules import Finding, Severity, ValidationReport, registered_rules, validate

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # model
    "PIDModel",
    "Equipment",
    "Port",
    "Stream",
    "Endpoint",
    "InlineItem",
    "Instrument",
    "ControlLoop",
    "Interlock",
    # pipeline
    "build_pid",
    "revise_pid",
    "BuildResult",
    "save_model",
    "load_model",
    # validation
    "validate",
    "ValidationReport",
    "Finding",
    "Severity",
    "registered_rules",
    # output
    "render_svg",
    "compute_layout",
    "write_package",
    "line_list_csv",
    "equipment_list_csv",
    "instrument_index_csv",
    "loop_schedule_csv",
    "relief_schedule_csv",
    "interlock_schedule_csv",
]
