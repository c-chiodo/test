"""The command line interface.

Only the commands that do not call the API are exercised here — ``build`` and
``revise`` are covered through the agent and pipeline tests.
"""

import json

import pytest

from pid.cli import main
from pid.examples import bleaching_unit
from pid.pipeline import save_model


@pytest.fixture()
def saved(tmp_path):
    return save_model(bleaching_unit(), tmp_path / "pid.json")


# -- example ---------------------------------------------------------------


def test_example_writes_a_full_package(tmp_path, capsys):
    assert main(["example", "--out", str(tmp_path)]) == 0
    written = {path.name for path in tmp_path.iterdir()}
    assert {"pid.json", "pid.svg", "findings.txt", "line_list.csv"} <= written
    assert "0 error(s)" in capsys.readouterr().out


def test_example_can_list_what_is_available(capsys):
    assert main(["example", "--list"]) == 0
    assert "bleaching" in capsys.readouterr().out


def test_an_unknown_example_is_reported_not_raised(capsys):
    assert main(["example", "nope"]) == 1
    assert "no example" in capsys.readouterr().err


# -- validate --------------------------------------------------------------


def test_validate_exits_zero_on_a_clean_drawing(saved, capsys):
    assert main(["validate", str(saved)]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_validate_exits_nonzero_when_there_are_errors(tmp_path, capsys):
    model = bleaching_unit()
    model.instrument_by_tag("PI-1207").attached_to = "nonsense"
    path = save_model(model, tmp_path / "broken.json")
    assert main(["validate", str(path)]) == 1
    assert "R004" in capsys.readouterr().out


def test_validate_can_emit_json_for_a_ci_step(tmp_path, capsys):
    model = bleaching_unit()
    model.streams[0].size_in = None
    path = save_model(model, tmp_path / "warn.json")
    main(["validate", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["warning"] >= 1
    assert payload["findings"][0]["rule_id"]


def test_a_missing_file_is_reported_cleanly(capsys):
    assert main(["validate", "/nonexistent/pid.json"]) == 2
    assert "no such file" in capsys.readouterr().err


# -- render and export ----------------------------------------------------


def test_render_writes_svg_next_to_the_model_by_default(saved):
    assert main(["render", str(saved)]) == 0
    assert (saved.with_suffix(".svg")).read_text().startswith("<svg")


def test_render_honours_an_explicit_output_path(saved, tmp_path):
    target = tmp_path / "out" / "drawing.svg"
    assert main(["render", str(saved), "-o", str(target)]) == 0
    assert target.exists()


def test_export_writes_the_schedules(saved, tmp_path):
    assert main(["export", str(saved), "-o", str(tmp_path / "sched")]) == 0
    assert (tmp_path / "sched" / "line_list.csv").exists()


def test_export_can_stream_one_schedule_to_stdout(saved, capsys):
    assert main(["export", str(saved), "--only", "line_list"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("line_number,")


def test_an_unknown_schedule_name_lists_the_valid_ones(saved, capsys):
    assert main(["export", str(saved), "--only", "nope"]) == 2
    assert "line_list" in capsys.readouterr().err


# -- rules and describe ---------------------------------------------------


def test_rules_lists_every_registered_rule(capsys):
    from pid.rules import registered_rules

    assert main(["rules"]) == 0
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == len(registered_rules())
    assert "R001" in out


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("FIC-1201", "Flow rate / Indicate / Control"),
        ('6"-PL-1201-CS150', "Process liquid"),
        ("P-1201A", "pump"),
    ],
)
def test_describe_explains_tags_line_numbers_and_equipment(tag, expected, capsys):
    assert main(["describe", tag]) == 0
    assert expected in capsys.readouterr().out


def test_describe_reports_the_role_a_tag_plays(capsys):
    main(["describe", "TV-1202"])
    assert "final element" in capsys.readouterr().out


def test_describe_rejects_something_that_is_not_a_tag(capsys):
    assert main(["describe", "hello world"]) == 1
    assert "not a recognisable" in capsys.readouterr().err
