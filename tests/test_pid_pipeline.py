"""Merging agent output, judging revisions, and persistence.

These cover the parts of the pipeline that decide what reaches the drawing, so
they run without an API key: agent calls are stubbed in
``test_pid_agents.py``, and here the agents' *output* is constructed directly.
"""

import pytest

from pid.agents.results import (
    InlineAddition,
    InstrumentationResult,
    IntakeResult,
    SafetyResult,
)
from pid.examples import bleaching_unit
from pid.model import (
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
from pid.pipeline import (
    apply_inline_additions,
    judge_revision,
    load_model,
    merge_instrumentation,
    merge_safety,
    save_model,
)
from pid.rules import validate


@pytest.fixture()
def model():
    return bleaching_unit()


# -- in-line additions ------------------------------------------------------


def test_a_component_is_appended_at_the_downstream_end(model):
    line = '3"-OIL-1211-CS150'
    before = len(model.stream_by_number(line).inline)
    skipped = apply_inline_additions(
        model, [InlineAddition(line_number=line, item=InlineItem(type="check_valve"))]
    )
    assert skipped == []
    inline = model.stream_by_number(line).inline
    assert len(inline) == before + 1
    assert inline[-1].type == "check_valve"


def test_a_component_can_be_inserted_at_a_position(model):
    line = '3"-OIL-1211-CS150'
    apply_inline_additions(
        model,
        [InlineAddition(line_number=line, item=InlineItem(type="strainer"), insert_at=0)],
    )
    assert model.stream_by_number(line).inline[0].type == "strainer"


def test_an_out_of_range_position_appends_rather_than_failing(model):
    line = '3"-OIL-1211-CS150'
    apply_inline_additions(
        model,
        [InlineAddition(line_number=line, item=InlineItem(type="strainer"), insert_at=99)],
    )
    assert model.stream_by_number(line).inline[-1].type == "strainer"


def test_a_component_for_a_line_that_does_not_exist_is_skipped_and_reported(model):
    skipped = apply_inline_additions(
        model,
        [InlineAddition(line_number='6"-PL-9999-CS150', item=InlineItem(type="check_valve"))],
    )
    assert len(skipped) == 1
    assert "does not exist" in skipped[0]


def test_a_component_reusing_an_existing_tag_is_skipped(model):
    skipped = apply_inline_additions(
        model,
        [
            InlineAddition(
                line_number='3"-OIL-1211-CS150',
                item=InlineItem(type="control_valve", tag="FV-1201", fail_position="fail_closed"),
            )
        ],
    )
    assert len(skipped) == 1
    assert "already used" in skipped[0]


def test_a_repeated_untagged_component_is_treated_as_a_duplicate(model):
    line = '3"-OIL-1211-CS150'  # already has one untagged gate valve
    skipped = apply_inline_additions(
        model, [InlineAddition(line_number=line, item=InlineItem(type="gate_valve"))]
    )
    assert len(skipped) == 1
    assert "duplicate" in skipped[0]


# -- merging instrumentation ------------------------------------------------


def test_instrumentation_merges_instruments_loops_and_valves(model):
    model.instruments = []
    model.loops = []
    result = InstrumentationResult(
        instruments=[
            Instrument(tag="FT-1250", description="New flow", attached_to='3"-OIL-1211-CS150'),
        ],
        loops=[
            ControlLoop(
                number="1250",
                description="New loop",
                controlled_variable="Flow",
                measurement_tags=["FT-1250"],
                controller_tag="FIC-1250",
                final_element_tag="FV-1250",
            )
        ],
        inline_additions=[
            InlineAddition(
                line_number='3"-OIL-1211-CS150',
                item=InlineItem(type="control_valve", tag="FV-1250", fail_position="fail_closed"),
            )
        ],
        notes=["Flow control added"],
    )
    skipped = merge_instrumentation(model, result)
    assert skipped == []
    assert model.instrument_by_tag("FT-1250") is not None
    assert model.loops[0].number == "1250"
    assert any(item.tag == "FV-1250" for _, item in model.inline_items())
    assert "Flow control added" in model.notes


def test_merging_the_same_instrumentation_twice_changes_nothing(model):
    result = InstrumentationResult(
        instruments=[Instrument(tag="PI-1207", description="Duplicate", attached_to="TK-1201")],
        loops=[
            ControlLoop(
                number="1201",
                description="Duplicate loop",
                controlled_variable="Flow",
                measurement_tags=["FT-1201"],
                controller_tag="FIC-1201",
                final_element_tag="FV-1201",
            )
        ],
    )
    before = (len(model.instruments), len(model.loops))
    skipped = merge_instrumentation(model, result)
    assert (len(model.instruments), len(model.loops)) == before
    assert len(skipped) == 2


# -- merging safety --------------------------------------------------------


def test_safety_adds_equipment_before_the_lines_that_need_it(model):
    result = SafetyResult(
        new_equipment=[
            Equipment(
                tag="OSBL-1299",
                name="To flare",
                kind="boundary",
                ports=[Port(name="N1", kind="inlet")],
            )
        ],
        new_streams=[
            Stream(
                number='2"-VE-1299-CS150',
                source=Endpoint(equipment="F-1201", port="N2"),
                destination=Endpoint(equipment="OSBL-1299", port="N1"),
                service="VE",
                size_in=2.0,
                spec="CS150",
                phase="vapor",
            )
        ],
        inline_additions=[
            InlineAddition(
                line_number='2"-VE-1299-CS150',
                item=InlineItem(type="relief_valve", tag="PSV-1299", set_pressure_psig=85.0),
            )
        ],
        instruments=[
            Instrument(
                tag="PSH-1299",
                description="Filter high pressure",
                attached_to="F-1201",
                location="plc",
                setpoint="80 psig",
            )
        ],
        interlocks=[
            Interlock(
                tag="I-1299",
                description="Filter high pressure stops the feed",
                initiators=["PSH-1299"],
                actions=["Stop P-1202"],
                trip_setpoint="80 psig",
            )
        ],
    )
    # The relief valve lands on a line created in the same pass, which only works
    # because equipment and streams are merged before in-line components.
    skipped = merge_safety(model, result)
    assert skipped == []
    assert any(item.tag == "PSV-1299" for _, item in model.inline_items())
    assert model.interlocks[-1].tag == "I-1299"
    assert validate(model).ok


# -- judging a revision ----------------------------------------------------


def _broken_and_fixed():
    """A drawing with one error, and the same drawing with it fixed."""
    broken = bleaching_unit()
    broken.instrument_by_tag("PI-1207").attached_to = "nonsense"  # trips R004
    fixed = bleaching_unit()
    return broken, fixed


def test_a_revision_that_fixes_an_error_is_accepted():
    broken, fixed = _broken_and_fixed()
    accept, reason = judge_revision(broken, validate(broken), fixed, validate(fixed))
    assert accept, reason


def test_a_revision_that_adds_errors_is_rejected():
    broken, fixed = _broken_and_fixed()
    accept, reason = judge_revision(fixed, validate(fixed), broken, validate(broken))
    assert not accept
    assert "errors rose" in reason


def test_a_revision_that_changes_nothing_measurable_is_rejected():
    model = bleaching_unit()
    accept, reason = judge_revision(model, validate(model), bleaching_unit(), validate(model))
    assert not accept
    assert "no measurable improvement" in reason


def test_a_revision_that_silently_deletes_equipment_is_rejected():
    broken, fixed = _broken_and_fixed()
    # Fixes the error, but also drops a filter nobody asked it to remove.
    fixed.equipment = [e for e in fixed.equipment if e.tag != "F-1202"]
    fixed.streams = [
        s
        for s in fixed.streams
        if "F-1202" not in (s.source.equipment, s.destination.equipment)
    ]
    accept, reason = judge_revision(broken, validate(broken), fixed, validate(fixed))
    assert not accept
    assert "F-1202" in reason


def test_deleting_equipment_a_finding_asked_about_is_allowed():
    # R201 explicitly suggests removing an unconnected item, so a revision that
    # does exactly that must not be rejected for losing equipment.
    before = bleaching_unit()
    before.equipment.append(
        Equipment(
            tag="P-1299",
            name="Orphan pump",
            kind="pump",
            design_pressure_psig=150.0,
            design_temp_f=250.0,
        )
    )
    after = bleaching_unit()
    before_report = validate(before)
    assert any(f.rule_id == "R201" and f.subject == "P-1299" for f in before_report.findings)
    accept, reason = judge_revision(before, before_report, after, validate(after))
    assert accept, reason


def test_trading_one_error_for_several_notes_is_still_an_improvement():
    broken, fixed = _broken_and_fixed()
    # Removing phase information adds three notes but the error is gone.
    for stream in fixed.streams[:3]:
        stream.phase = None
    accept, reason = judge_revision(broken, validate(broken), fixed, validate(fixed))
    assert accept, reason


# -- persistence -----------------------------------------------------------


def test_save_and_load_round_trip(model, tmp_path):
    path = save_model(model, tmp_path / "nested" / "pid.json")
    assert path.exists()
    reloaded = load_model(path)
    assert reloaded.model_dump() == model.model_dump()
    assert validate(reloaded).ok


def test_saved_json_ends_with_a_newline(model, tmp_path):
    path = save_model(model, tmp_path / "pid.json")
    assert path.read_text().endswith("\n")


def test_intake_result_maps_onto_the_drawing_model():
    result = IntakeResult(
        title="Tiny unit",
        description="One tank",
        equipment=[Equipment(tag="TK-1", name="Tank", kind="tank")],
        streams=[],
        assumptions=["assumed something"],
    )
    model = PIDModel(
        title=result.title,
        description=result.description,
        equipment=result.equipment,
        streams=result.streams,
        assumptions=list(result.assumptions),
    )
    assert model.equipment_by_tag("TK-1").name == "Tank"
    assert model.assumptions == ["assumed something"]
