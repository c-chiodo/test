"""Command line interface for the P&ID builder.

    pid build "description of the process" --area 1200 --out drawings/bleaching
    pid validate drawings/bleaching/pid.json
    pid render   drawings/bleaching/pid.json -o bleaching.svg
    pid export   drawings/bleaching/pid.json -o drawings/bleaching
    pid revise   drawings/bleaching/pid.json "add a second filter in parallel"
    pid describe FIC-1201

Only ``build`` and ``revise`` call the API. Validation, rendering and export are
pure local work on the saved model, so a drawing can be re-checked and
re-rendered as often as you like for free.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .exports import EXPORTERS, write_package
from .pipeline import build_pid, load_model, revise_pid, save_model
from .render import render_svg
from .rules import registered_rules, validate

MODEL_FILENAME = "pid.json"
DRAWING_FILENAME = "pid.svg"
FINDINGS_FILENAME = "findings.txt"


def _progress(message: str) -> None:
    print(f"  … {message}", file=sys.stderr)


def _settings_from_args(args: argparse.Namespace):
    from .agents import AgentSettings

    return AgentSettings(
        model=args.model,
        effort=args.effort,
        max_tokens=args.max_tokens,
        refusal_fallback=not args.no_fallback,
        house_rules=args.house_rules or "",
    )


def _add_model_args(parser: argparse.ArgumentParser) -> None:
    from .agents import DEFAULT_MODEL

    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model (default {DEFAULT_MODEL})")
    parser.add_argument(
        "--effort",
        default="high",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="reasoning effort (default high)",
    )
    parser.add_argument("--max-tokens", type=int, default=16000, help="output token cap per pass")
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="do not retry a safety-classifier decline on a fallback model",
    )
    parser.add_argument(
        "--house-rules",
        help="extra conventions to give every agent, e.g. a client tagging standard",
    )


def _write_outputs(model, directory: Path, report) -> list[Path]:
    written = [save_model(model, directory / MODEL_FILENAME)]
    drawing = directory / DRAWING_FILENAME
    drawing.write_text(render_svg(model), encoding="utf-8")
    written.append(drawing)
    findings = directory / FINDINGS_FILENAME
    findings.write_text(f"{report.summary()}\n\n{report.to_text()}\n", encoding="utf-8")
    written.append(findings)
    written += write_package(model, directory)
    return written


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_build(args: argparse.Namespace) -> int:
    description = (
        Path(args.from_file).read_text(encoding="utf-8") if args.from_file else args.description
    )
    if not description or not description.strip():
        print("error: give a process description, or --from-file", file=sys.stderr)
        return 2

    result = build_pid(
        description,
        settings=_settings_from_args(args),
        area=args.area,
        equipment_hints=args.equipment or None,
        control_philosophy=args.control or "",
        hazards=args.hazards or "",
        review_rounds=args.review_rounds,
        skip_instrumentation=args.skip_instrumentation,
        skip_safety=args.skip_safety,
        progress=_progress,
    )

    directory = Path(args.out)
    written = _write_outputs(result.model, directory, result.report)

    print(result.summary())
    print()
    for path in written:
        print(f"wrote {path}")
    if result.report.errors:
        print()
        print(f"{len(result.report.errors)} error(s) remain — see {directory / FINDINGS_FILENAME}")
        return 1
    return 0


def cmd_revise(args: argparse.Namespace) -> int:
    model = load_model(args.model_file)
    result = revise_pid(
        model,
        args.guidance,
        settings=_settings_from_args(args),
        review_rounds=args.review_rounds,
        progress=_progress,
    )
    directory = Path(args.out) if args.out else Path(args.model_file).parent
    written = _write_outputs(result.model, directory, result.report)

    print(result.summary())
    print()
    for path in written:
        print(f"wrote {path}")
    return 1 if result.report.errors else 0


def cmd_validate(args: argparse.Namespace) -> int:
    model = load_model(args.model_file)
    report = validate(model)
    if args.json:
        import json

        print(json.dumps({"summary": report.counts(), "findings": report.to_dicts()}, indent=2))
    else:
        print(f"{model.title} — {report.summary()}")
        print()
        print(report.to_text())
    return 1 if report.errors else 0


def cmd_render(args: argparse.Namespace) -> int:
    model = load_model(args.model_file)
    svg = render_svg(model)
    target = Path(args.out) if args.out else Path(args.model_file).with_suffix(".svg")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(svg, encoding="utf-8")
    print(f"wrote {target}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    model = load_model(args.model_file)
    if args.only:
        filename = args.only if args.only.endswith(".csv") else f"{args.only}.csv"
        exporter = EXPORTERS.get(filename)
        if exporter is None:
            print(
                f"error: no exporter {args.only!r}; choose from "
                f"{', '.join(sorted(name[:-4] for name in EXPORTERS))}",
                file=sys.stderr,
            )
            return 2
        sys.stdout.write(exporter(model))
        return 0
    written = write_package(model, args.out or Path(args.model_file).parent)
    for path in written:
        print(f"wrote {path}")
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    for rule_id, title in registered_rules():
        print(f"{rule_id}  {title}")
    return 0


def cmd_example(args: argparse.Namespace) -> int:
    from .examples import EXAMPLES, load_example

    if args.list:
        for name in sorted(EXAMPLES):
            print(name)
        return 0
    model = load_example(args.name)
    directory = Path(args.out)
    report = validate(model)
    for path in _write_outputs(model, directory, report):
        print(f"wrote {path}")
    print()
    print(f"{model.title} — {report.summary()}")
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    from .tagging import (
        describe_instrument_tag,
        parse_equipment_tag,
        parse_line_number,
        validate_instrument_tag,
        validate_line_number,
    )

    tag = args.tag.strip()

    line = parse_line_number(tag)
    if line is not None:
        from .standards import PIPE_SPECS, SERVICE_CODES

        print(f"{tag} — line number")
        print(f"  size      {line.size_in:g}\"")
        print(f"  service   {line.service} ({SERVICE_CODES.get(line.service, 'unrecognised')})")
        print(f"  sequence  {line.sequence}")
        print(f"  spec      {line.spec or '—'} ({PIPE_SPECS.get(line.spec, 'unrecognised')})")
        for message in validate_line_number(tag).warnings:
            print(f"  ! {message}")
        return 0

    instrument = validate_instrument_tag(tag)
    if instrument.ok or instrument.warnings:
        print(f"{tag} — instrument")
        print(f"  function  {describe_instrument_tag(tag)}")
        from .tagging import parse_instrument_tag

        parsed = parse_instrument_tag(tag)
        if parsed:
            print(f"  loop      {parsed.number}")
            roles = [
                name
                for name, flag in (
                    ("controller", parsed.is_controller),
                    ("transmitter", parsed.is_transmitter),
                    ("primary element", parsed.is_sensor),
                    ("final element", parsed.is_final_element),
                    ("switch", parsed.is_switch),
                    ("alarm", parsed.is_alarm),
                    ("relief device", parsed.is_relief_device),
                )
                if flag
            ]
            print(f"  role      {', '.join(roles) if roles else 'indication only'}")
        for message in instrument.warnings:
            print(f"  ! {message}")
        return 0

    equipment = parse_equipment_tag(tag)
    if equipment is not None:
        print(f"{tag} — equipment")
        print(f"  prefix    {equipment.prefix}")
        print(f"  class     {equipment.kind or 'unrecognised prefix'}")
        return 0

    print(f"{tag} is not a recognisable instrument tag, equipment tag or line number", file=sys.stderr)
    for message in instrument.errors:
        print(f"  {message}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pid",
        description="Build, check, draw and export piping and instrumentation diagrams.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build a P&ID from a process description")
    build.add_argument("description", nargs="?", help="the process description")
    build.add_argument("--from-file", help="read the description from a file instead")
    build.add_argument("--out", default="pid-out", help="output directory (default pid-out)")
    build.add_argument("--area", type=int, default=1000, help="tag numbering block, e.g. 1200")
    build.add_argument(
        "--equipment",
        action="append",
        metavar="ITEM",
        help="equipment that must appear; repeatable",
    )
    build.add_argument("--control", help="control philosophy guidance for the instrumentation pass")
    build.add_argument("--hazards", help="known hazards for the safety pass")
    build.add_argument("--review-rounds", type=int, default=2, help="review passes (default 2)")
    build.add_argument("--skip-instrumentation", action="store_true", help="equipment graph only")
    build.add_argument("--skip-safety", action="store_true", help="skip the safeguarding pass")
    _add_model_args(build)
    build.set_defaults(func=cmd_build)

    revise = subparsers.add_parser("revise", help="apply a change to an existing drawing")
    revise.add_argument("model_file", help="path to pid.json")
    revise.add_argument("guidance", help="what to change")
    revise.add_argument("--out", help="output directory (defaults to the model's directory)")
    revise.add_argument("--review-rounds", type=int, default=1)
    _add_model_args(revise)
    revise.set_defaults(func=cmd_revise)

    check = subparsers.add_parser("validate", help="run the rule engine against a saved drawing")
    check.add_argument("model_file")
    check.add_argument("--json", action="store_true", help="emit findings as JSON")
    check.set_defaults(func=cmd_validate)

    render = subparsers.add_parser("render", help="draw a saved model as SVG")
    render.add_argument("model_file")
    render.add_argument("-o", "--out", help="output path (default alongside the model)")
    render.set_defaults(func=cmd_render)

    export = subparsers.add_parser("export", help="write the line list, index and schedules")
    export.add_argument("model_file")
    export.add_argument("-o", "--out", help="output directory")
    export.add_argument(
        "--only",
        help="write one schedule to stdout: "
        + ", ".join(sorted(name[:-4] for name in EXPORTERS)),
    )
    export.set_defaults(func=cmd_export)

    rules = subparsers.add_parser("rules", help="list the validation rules")
    rules.set_defaults(func=cmd_rules)

    describe = subparsers.add_parser("describe", help="explain a tag or line number")
    describe.add_argument("tag")
    describe.set_defaults(func=cmd_describe)

    example = subparsers.add_parser(
        "example", help="write out a worked P&ID — no API call, nothing to configure"
    )
    example.add_argument("name", nargs="?", default="bleaching")
    example.add_argument("--out", default="pid-example", help="output directory")
    example.add_argument("--list", action="store_true", help="list the available examples")
    example.set_defaults(func=cmd_example)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc.filename}: no such file", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    except Exception as exc:  # noqa: BLE001 - the CLI reports, it does not traceback
        from .agents import AgentError

        prefix = "agent error" if isinstance(exc, AgentError) else "error"
        print(f"{prefix}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
