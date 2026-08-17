"""The tabular deliverables: line list, instrument index and schedules."""

import csv
import io

import pytest

from pid.examples import bleaching_unit
from pid.exports import (
    EQUIPMENT_COLUMNS,
    EXPORTERS,
    INSTRUMENT_COLUMNS,
    LINE_COLUMNS,
    equipment_list_csv,
    instrument_index_csv,
    interlock_schedule_csv,
    line_list_csv,
    loop_schedule_csv,
    relief_schedule_csv,
    write_package,
)
from pid.model import PIDModel


@pytest.fixture()
def model():
    return bleaching_unit()


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


# -- equipment list ---------------------------------------------------------


def test_the_equipment_list_covers_every_process_item(model):
    parsed = rows(equipment_list_csv(model))
    assert {row["tag"] for row in parsed} == {e.tag for e in model.process_equipment()}


def test_boundary_connectors_are_not_equipment_to_be_bought(model):
    parsed = rows(equipment_list_csv(model))
    assert not any(row["tag"].startswith("OSBL") for row in parsed)


def test_the_equipment_list_carries_the_lines_into_and_out_of_each_item(model):
    parsed = {row["tag"]: row for row in rows(equipment_list_csv(model))}
    assert '4"-OIL-1206-CS150J' in parsed["E-1201"]["outlet_lines"]
    assert '4"-OIL-1204-CS150' in parsed["E-1201"]["inlet_lines"]


def test_equipment_columns_are_stable(model):
    assert rows(equipment_list_csv(model))[0].keys() == dict.fromkeys(EQUIPMENT_COLUMNS).keys()


# -- line list --------------------------------------------------------------


def test_the_line_list_covers_every_line(model):
    parsed = rows(line_list_csv(model))
    assert {row["line_number"] for row in parsed} == {s.number for s in model.streams}
    assert parsed[0].keys() == dict.fromkeys(LINE_COLUMNS).keys()


def test_service_and_spec_codes_are_expanded_for_the_reader(model):
    parsed = {row["line_number"]: row for row in rows(line_list_csv(model))}
    steam = parsed['2"-ST-1207-CS300']
    assert steam["service_description"] == "Steam"
    assert steam["spec_description"] == "Carbon steel, ASME Class 300"


def test_inline_components_are_listed_in_flow_order(model):
    parsed = {row["line_number"]: row for row in rows(line_list_csv(model))}
    components = parsed['4"-OIL-1204-CS150']["inline_components"]
    assert components.index("check_valve") < components.index("FV-1201")


def test_the_line_list_is_sorted_so_revisions_diff_cleanly(model):
    numbers = [row["line_number"] for row in rows(line_list_csv(model))]
    assert numbers == sorted(numbers)


# -- instrument index -------------------------------------------------------


def test_the_index_covers_every_instrument_and_expands_its_function(model):
    parsed = {row["tag"]: row for row in rows(instrument_index_csv(model))}
    assert set(parsed) == {i.tag for i in model.instruments}
    assert parsed["FIC-1201"]["function"] == "Flow rate / Indicate / Control"
    assert parsed["LSLL-1206"]["function"].startswith("Level / Switch / Low")
    assert parsed.popitem()[1].keys() == dict.fromkeys(INSTRUMENT_COLUMNS).keys()


def test_the_loop_number_is_filled_in_from_the_tag_when_not_stated(model):
    # PI-1207 carries no explicit loop field; the index derives it from the tag.
    assert model.instrument_by_tag("PI-1207").loop is None
    parsed = {row["tag"]: row for row in rows(instrument_index_csv(model))}
    assert parsed["PI-1207"]["loop"] == "1207"


# -- loop schedule ----------------------------------------------------------


def test_the_loop_schedule_joins_the_valve_fail_position(model):
    # Fail position lives on the valve, but a reviewer looks for it here.
    parsed = {row["loop"]: row for row in rows(loop_schedule_csv(model))}
    assert parsed["1201"]["final_element"] == "FV-1201"
    assert parsed["1201"]["fail_position"] == "fail_closed"
    assert parsed["1202"]["measurements"] == "TE-1202; TT-1202"


# -- relief schedule --------------------------------------------------------


def test_the_relief_schedule_puts_set_pressure_next_to_design_pressure(model):
    parsed = {row["tag"]: row for row in rows(relief_schedule_csv(model))}
    psv = parsed["PSV-1201"]
    assert psv["protects"] == "V-1201"
    assert float(psv["set_pressure_psig"]) == 45.0
    assert float(psv["equipment_design_pressure_psig"]) == 50.0
    # The whole point of the column pairing: the margin is checkable at a glance.
    assert float(psv["set_pressure_psig"]) <= float(psv["equipment_design_pressure_psig"])
    assert psv["discharges_to"] == "OSBL-1206"


def test_every_relief_device_on_the_drawing_reaches_the_schedule(model):
    from pid.standards import RELIEF_DEVICE_TYPES

    expected = {
        item.tag for _, item in model.inline_items() if item.type in RELIEF_DEVICE_TYPES
    }
    assert {row["tag"] for row in rows(relief_schedule_csv(model))} == expected


# -- interlock schedule -----------------------------------------------------


def test_the_interlock_schedule_reads_as_cause_and_effect(model):
    parsed = {row["tag"]: row for row in rows(interlock_schedule_csv(model))}
    assert parsed["I-1201"]["initiators"] == "LSLL-1206"
    assert "Stop P-1201A" in parsed["I-1201"]["actions"]
    assert parsed["I-1203"]["sil"] == "SIL 1"


# -- the package ------------------------------------------------------------


def test_write_package_writes_every_non_empty_schedule(model, tmp_path):
    written = write_package(model, tmp_path)
    assert {path.name for path in written} == set(EXPORTERS)
    for path in written:
        assert path.read_text().count("\n") > 1


def test_empty_schedules_are_skipped_rather_than_written_as_headers(tmp_path):
    # A drawing with no interlocks should not ship an interlock schedule that is
    # nothing but a header row.
    model = PIDModel(title="Nothing much")
    assert write_package(model, tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_a_filename_stem_prefixes_every_file(model, tmp_path):
    written = write_package(model, tmp_path, stem="PID-1200-001")
    assert all(path.name.startswith("PID-1200-001_") for path in written)


def test_none_becomes_an_empty_cell_not_the_string_none(model):
    model.streams[0].size_in = None
    parsed = {row["line_number"]: row for row in rows(line_list_csv(model))}
    assert parsed['8"-OIL-1201-CS150']["size_in"] == ""
