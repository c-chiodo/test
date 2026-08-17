"""The validation rule engine.

Two halves: the worked example must come back clean, and each rule must actually
fire when its condition is broken. A rule that never fires is worse than no rule
— it reads like coverage the drawing does not have.
"""

import pytest

from pid.examples import bleaching_unit
from pid.model import Endpoint, Equipment, InlineItem, Instrument, Interlock, Port, Stream
from pid.rules import Severity, registered_rules, validate


@pytest.fixture()
def model():
    return bleaching_unit()


def fired(model, rule_id):
    """Findings from one rule."""
    return [f for f in validate(model).findings if f.rule_id == rule_id]


# -- the baseline -----------------------------------------------------------


def test_the_worked_example_validates_clean(model):
    report = validate(model)
    assert report.findings == [], report.to_text()


def test_every_rule_is_registered_once():
    ids = [rule_id for rule_id, _ in registered_rules()]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 20


def test_report_helpers_summarise_and_serialise(model):
    report = validate(model)
    assert report.ok
    assert report.counts() == {"error": 0, "warning": 0, "info": 0}
    assert report.to_text() == "No findings."
    assert report.to_dicts() == []


# -- R0xx referential integrity --------------------------------------------


def test_duplicate_tags_are_an_error(model):
    model.instruments.append(
        Instrument(tag="PI-1207", description="Duplicated on purpose", attached_to="TK-1201")
    )
    findings = fired(model, "R001")
    assert findings and findings[0].severity is Severity.ERROR
    assert "PI-1207" in findings[0].subject


def test_a_line_to_nowhere_is_an_error(model):
    model.streams[0].destination = Endpoint(equipment="TK-9999")
    assert any("TK-9999" in f.message for f in fired(model, "R002"))


def test_a_line_landing_on_a_nozzle_that_does_not_exist_warns(model):
    model.streams[0].destination = Endpoint(equipment="TK-1201", port="N99")
    findings = fired(model, "R003")
    assert findings and findings[0].severity is Severity.WARNING


def test_an_instrument_attached_to_nothing_is_an_error(model):
    model.instruments[0].attached_to = "not-a-tag"
    assert fired(model, "R004")


def test_a_loop_referencing_a_missing_instrument_is_an_error(model):
    model.loops[0].measurement_tags = ["FT-9999"]
    assert any("FT-9999" in f.message for f in fired(model, "R005"))


def test_an_interlock_with_no_initiator_or_no_action_is_an_error(model):
    model.interlocks.append(
        Interlock(tag="I-1299", description="Does nothing", initiators=[], actions=[])
    )
    findings = fired(model, "R006")
    assert {f.message.split(" ")[1] for f in findings} >= {"no"}
    assert len([f for f in findings if f.severity is Severity.ERROR]) == 2


def test_an_interlock_initiator_that_is_not_on_the_drawing_warns(model):
    model.interlocks[0].initiators = ["LSLL-9999"]
    assert any(f.severity is Severity.WARNING for f in fired(model, "R006"))


# -- R1xx tag conventions ---------------------------------------------------


def test_a_bad_instrument_tag_is_reported(model):
    model.instruments[0].tag = "nonsense"
    assert fired(model, "R101")


def test_a_pump_tagged_like_a_tank_warns(model):
    model.equipment_by_tag("P-1202").tag = "TK-1202"
    # The stream endpoints still point at P-1202, so R002 also fires; we only
    # care that the prefix mismatch was caught.
    assert fired(model, "R102")


def test_a_bad_line_number_is_reported(model):
    model.streams[0].number = "six inch oil line"
    assert fired(model, "R103")


def test_an_unknown_equipment_class_warns(model):
    model.equipment_by_tag("V-1201").kind = "thingamajig"
    assert fired(model, "R104")


def test_an_unknown_inline_component_warns(model):
    model.streams[1].inline.append(InlineItem(type="wormhole"))
    assert fired(model, "R105")


def test_unknown_location_and_signal_codes_warn(model):
    model.instruments[0].location = "somewhere"
    model.instruments[0].signal = "telepathy"
    assert len(fired(model, "R106")) == 2


def test_an_instrument_numbered_off_its_loop_warns(model):
    model.loops[0].measurement_tags = ["FT-1299"]
    model.instruments[0].tag = "FT-1299"
    assert fired(model, "R107")


# -- R2xx topology ----------------------------------------------------------


def test_orphan_equipment_is_an_error(model):
    model.equipment.append(
        Equipment(tag="P-1299", name="Unconnected pump", kind="pump", design_pressure_psig=150.0, design_temp_f=250.0)
    )
    findings = fired(model, "R201")
    assert any(f.subject == "P-1299" and f.severity is Severity.ERROR for f in findings)


def test_a_pump_with_no_discharge_is_an_error(model):
    model.streams = [s for s in model.streams if s.source.equipment != "P-1202"]
    assert any("discharge" in f.message for f in fired(model, "R202"))


def test_material_flowing_into_a_dead_end_warns(model):
    model.streams = [s for s in model.streams if s.source.equipment != "F-1202"]
    findings = fired(model, "R203")
    assert any(f.subject == "F-1202" for f in findings)


def test_a_tank_is_allowed_to_be_a_terminus(model):
    # Removing the day tank's outlets leaves it a dead end, which is legitimate
    # for inventory equipment and must not be reported by R203.
    model.streams = [s for s in model.streams if s.source.equipment != "TK-1201"]
    assert not [f for f in fired(model, "R203") if f.subject == "TK-1201"]


def test_a_line_starting_and_ending_on_the_same_nozzle_is_an_error(model):
    model.streams[0].source = Endpoint(equipment="TK-1201", port="N1")
    model.streams[0].destination = Endpoint(equipment="TK-1201", port="N1")
    findings = fired(model, "R204")
    assert findings[0].severity is Severity.ERROR


def test_a_recirculation_line_is_noted_not_flagged(model):
    model.streams[0].source = Endpoint(equipment="TK-1201", port="N2")
    model.streams[0].destination = Endpoint(equipment="TK-1201", port="N1")
    findings = fired(model, "R204")
    assert findings[0].severity is Severity.INFO


# -- R3xx equipment completeness -------------------------------------------


def test_a_vessel_with_no_level_measurement_is_an_error(model):
    # Any L-prefixed instrument on the vessel counts, so both the transmitter
    # and the controller have to go for the rule to fire.
    model.instruments = [i for i in model.instruments if i.tag not in {"LT-1204", "LIC-1204"}]
    findings = fired(model, "R301")
    assert any(f.subject == "V-1201" and f.severity is Severity.ERROR for f in findings)


def test_a_level_controller_alone_satisfies_the_level_requirement(model):
    # R301 asks "is level measured", not "is it measured well" — a bare
    # controller with no transmitter is R403's problem, not R301's.
    model.instruments = [i for i in model.instruments if i.tag != "LT-1204"]
    assert not [f for f in fired(model, "R301") if f.subject == "V-1201"]


def test_a_vessel_with_no_relief_device_is_an_error(model):
    model.streams = [s for s in model.streams if s.number != '2"-VE-1214-CS150']
    findings = fired(model, "R302")
    assert any(f.subject == "V-1201" and f.severity is Severity.ERROR for f in findings)


def test_missing_design_conditions_warn(model):
    model.equipment_by_tag("V-1201").design_pressure_psig = None
    model.equipment_by_tag("V-1201").design_temp_f = None
    findings = fired(model, "R303")
    assert findings and "design pressure and design temperature" in findings[0].message


def test_a_heat_exchanger_with_no_temperature_indication_warns(model):
    model.instruments = [i for i in model.instruments if not i.tag.startswith(("TE-", "TT-", "TIC-", "TSH-"))]
    assert any(f.subject == "E-1201" for f in fired(model, "R304"))


def test_a_pump_with_no_isolation_warns(model):
    for stream in model.streams_to("P-1202") + model.streams_from("P-1202"):
        stream.inline = [i for i in stream.inline if i.type != "gate_valve"]
    findings = fired(model, "R305")
    assert len([f for f in findings if f.subject == "P-1202"]) == 2


def test_a_pump_with_no_check_valve_on_discharge_warns(model):
    for stream in model.streams_from("P-1202"):
        stream.inline = [i for i in stream.inline if i.type != "check_valve"]
    assert any(f.subject == "P-1202" for f in fired(model, "R306"))


def test_a_pump_with_no_discharge_gauge_is_a_note(model):
    model.instruments = [i for i in model.instruments if i.tag != "PI-1215"]
    findings = [f for f in fired(model, "R307") if f.subject == "P-1202"]
    assert findings and findings[0].severity is Severity.INFO


# -- R4xx control and safety logic -----------------------------------------


def test_a_control_valve_with_no_fail_position_is_an_error(model):
    for _, item in model.inline_items():
        if item.tag == "FV-1201":
            item.fail_position = None
    findings = fired(model, "R401")
    assert findings[0].severity is Severity.ERROR


def test_an_untagged_control_valve_is_an_error(model):
    model.streams[1].inline.append(InlineItem(type="control_valve", fail_position="fail_closed"))
    assert any(f.severity is Severity.ERROR for f in fired(model, "R402"))


def test_a_control_valve_no_loop_drives_warns(model):
    model.loops = [loop for loop in model.loops if loop.number != "1201"]
    findings = fired(model, "R402")
    assert any(f.subject == "FV-1201" for f in findings)


def test_a_loop_with_no_measurement_or_element_is_an_error(model):
    model.loops[0].measurement_tags = []
    model.loops[0].final_element_tag = ""
    findings = fired(model, "R403")
    assert len([f for f in findings if f.severity is Severity.ERROR]) >= 2


def test_a_controller_tag_without_a_c_warns(model):
    model.loops[0].controller_tag = "FT-1201"
    assert any("function letter" in f.message for f in fired(model, "R403"))


def test_a_cascade_from_a_loop_that_is_not_here_warns(model):
    model.loops[0].cascade_from = "9999"
    assert fired(model, "R404")


def test_a_relief_valve_set_above_design_pressure_is_an_error(model):
    for _, item in model.inline_items():
        if item.tag == "PSV-1201":
            item.set_pressure_psig = 75.0  # V-1201 is designed for 50 psig
    findings = fired(model, "R405")
    assert findings and findings[0].severity is Severity.ERROR
    assert "design pressure" in findings[0].message


def test_a_relief_valve_with_no_set_pressure_warns(model):
    for _, item in model.inline_items():
        if item.tag == "PSV-1201":
            item.set_pressure_psig = None
    assert any(f.severity is Severity.WARNING for f in fired(model, "R405"))


def test_a_relief_discharging_into_process_equipment_warns(model):
    # Routing the bleacher PSV into the feed tank moves the overpressure rather
    # than removing it.
    stream = model.stream_by_number('2"-VE-1214-CS150')
    stream.destination = Endpoint(equipment="TK-1201", port="N1")
    findings = fired(model, "R406")
    assert findings and findings[0].severity is Severity.WARNING


def test_a_relief_discharging_to_a_knockout_drum_is_accepted(model):
    model.equipment.append(
        Equipment(
            tag="V-1299",
            name="Relief knockout drum",
            kind="vessel",
            design_pressure_psig=50.0,
            design_temp_f=250.0,
            ports=[Port(name="N1", kind="inlet")],
        )
    )
    stream = model.stream_by_number('2"-VE-1214-CS150')
    stream.destination = Endpoint(equipment="V-1299", port="N1")
    assert not fired(model, "R406")


def test_a_switch_with_no_setpoint_warns(model):
    model.instrument_by_tag("PSH-1214").setpoint = ""
    assert fired(model, "R407")


def test_a_trip_switch_no_interlock_uses_is_a_note(model):
    model.interlocks = []
    findings = fired(model, "R408")
    assert findings and all(f.severity is Severity.INFO for f in findings)


# -- R5xx line completeness ------------------------------------------------


def test_a_line_with_no_size_warns(model):
    model.streams[0].size_in = None
    assert fired(model, "R501")


def test_a_size_that_disagrees_with_the_line_number_is_an_error(model):
    model.streams[0].size_in = 3.0  # the number says 8"
    findings = fired(model, "R502")
    assert findings and findings[0].severity is Severity.ERROR


def test_a_line_with_no_phase_is_a_note(model):
    model.streams[0].phase = None
    findings = fired(model, "R503")
    assert findings[0].severity is Severity.INFO


def test_a_service_that_disagrees_with_the_line_number_warns(model):
    model.streams[0].service = "CW"  # the number says OIL
    assert fired(model, "R504")


# -- a drawing that is wrong in many ways ----------------------------------


def test_a_skeletal_drawing_reports_across_several_rule_groups():
    from pid.model import PIDModel

    model = PIDModel(
        title="Barely a drawing",
        equipment=[
            Equipment(tag="V-1", name="Mystery vessel", kind="vessel", ports=[Port(name="N1", kind="outlet")]),
            Equipment(tag="TK-1", name="Mystery tank", kind="tank"),
        ],
        streams=[
            Stream(
                number="a line",
                source=Endpoint(equipment="V-1", port="N1"),
                destination=Endpoint(equipment="TK-1"),
                service="PL",
            )
        ],
    )
    report = validate(model)
    groups = {f.rule_id[:2] for f in report.findings}
    # Tag conventions, equipment completeness and line completeness should all
    # have something to say about this.
    assert {"R1", "R3", "R5"} <= groups
    assert not report.ok
