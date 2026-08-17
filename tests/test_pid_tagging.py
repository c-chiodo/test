"""Tag grammar: ISA-5.1 instrument tags, equipment tags and line numbers."""

import pytest

from pid.tagging import (
    format_line_number,
    format_size,
    describe_instrument_tag,
    next_equipment_tag,
    parse_equipment_tag,
    parse_instrument_tag,
    parse_line_number,
    validate_equipment_tag,
    validate_instrument_tag,
    validate_line_number,
)


# -- instrument tags --------------------------------------------------------


@pytest.mark.parametrize(
    "tag,letters,number,suffix",
    [
        ("FIC-1201", "FIC", "1201", ""),
        ("TE-1201A", "TE", "1201", "A"),
        ("LSHH-1101", "LSHH", "1101", ""),
        ("12-PDT-304", "PDT", "304", ""),
        ("PSV-1105", "PSV", "1105", ""),
    ],
)
def test_parse_instrument_tag_splits_letters_number_suffix(tag, letters, number, suffix):
    parsed = parse_instrument_tag(tag)
    assert parsed is not None
    assert (parsed.letters, parsed.number, parsed.suffix) == (letters, number, suffix)


def test_area_prefix_is_kept_separate_from_the_loop_number():
    parsed = parse_instrument_tag("12-PDT-304")
    assert parsed.area == "12"
    assert parsed.loop == "304"


@pytest.mark.parametrize("tag", ["FIC1201", "fic-1201", "FIC-", "-1201", "FIC-12A01", ""])
def test_malformed_instrument_tags_are_rejected(tag):
    assert parse_instrument_tag(tag) is None
    assert validate_instrument_tag(tag).errors


def test_roles_are_derived_from_the_function_letters():
    controller = parse_instrument_tag("TIC-1202")
    assert controller.is_controller and not controller.is_final_element
    assert parse_instrument_tag("TV-1202").is_final_element
    assert parse_instrument_tag("TE-1202").is_sensor
    assert parse_instrument_tag("TT-1202").is_transmitter
    assert parse_instrument_tag("PSH-1105").is_switch
    assert parse_instrument_tag("TAH-1105").is_alarm
    assert parse_instrument_tag("PSV-1105").is_relief_device


@pytest.mark.parametrize("tag", ["FIC-1201", "PDT-1201", "LSLL-1101", "TE-1201A", "LG-1101"])
def test_conventional_tags_validate_without_complaint(tag):
    issues = validate_instrument_tag(tag)
    assert issues.ok, issues.errors
    assert not issues.warnings, issues.warnings


def test_every_letter_is_a_legal_measured_variable():
    # ISA-5.1 assigns all 26 letters as first letters (several as "user's
    # choice"), so a structurally valid tag can never fail on its first letter —
    # 'QQ' is quantity/totalise and is legal. The grammar, not the table, is what
    # rejects nonsense first letters.
    assert validate_instrument_tag("QQ-1201").ok


def test_a_letter_with_no_succeeding_meaning_is_an_error():
    # B (burner) and J (power) are first-letter-only in ISA-5.1.
    assert validate_instrument_tag("FB-1201").errors
    assert validate_instrument_tag("FJ-1201").errors


def test_a_tag_with_no_function_letter_is_an_error():
    # 'P-1201' says pressure but not what the device does with it.
    assert validate_instrument_tag("P-1201").errors


def test_a_tag_of_only_modifiers_is_an_error():
    # 'PH' is pressure-high with no indicate, transmit, switch or control.
    assert validate_instrument_tag("PH-1201").errors


def test_a_misplaced_variable_modifier_warns():
    # The differential modifier belongs right after the first letter: PDT, not PTD.
    assert validate_instrument_tag("PTD-1201").warnings


def test_high_low_modifiers_belong_at_the_end():
    assert not validate_instrument_tag("PSH-1201").warnings
    assert validate_instrument_tag("PHS-1201").warnings


def test_combining_a_controller_and_a_primary_element_warns():
    assert validate_instrument_tag("TEC-1202").warnings


def test_describe_expands_the_letters_into_prose():
    assert describe_instrument_tag("FIC-1201") == "Flow rate / Indicate / Control"
    assert "Unrecognised" in describe_instrument_tag("not-a-tag")


# -- equipment tags ---------------------------------------------------------


def test_equipment_tags_split_into_prefix_number_suffix():
    parsed = parse_equipment_tag("P-1201A")
    assert (parsed.prefix, parsed.number, parsed.suffix) == ("P", "1201", "A")
    assert parsed.kind == "pump"


def test_a_prefix_that_disagrees_with_the_equipment_class_warns():
    assert validate_equipment_tag("P-1201", "pump").ok
    assert not validate_equipment_tag("P-1201", "pump").warnings
    assert validate_equipment_tag("P-1201", "tank").warnings


def test_next_equipment_tag_continues_the_series():
    assert next_equipment_tag("pump", ["P-1201", "P-1202"]) == "P-1203"
    # Only tags of the same class count towards the series.
    assert next_equipment_tag("tank", ["P-1201"], area=1100) == "TK-1101"


# -- line numbers -----------------------------------------------------------


def test_line_numbers_split_into_size_service_sequence_spec():
    parsed = parse_line_number('6"-PL-1201-CS150')
    assert parsed.size_in == 6.0
    assert (parsed.service, parsed.sequence, parsed.spec) == ("PL", "1201", "CS150")


def test_fractional_sizes_parse():
    assert parse_line_number('1-1/2"-ST-1310-CS300').size_in == 1.5
    assert parse_line_number('3/4"-IA-1401-SS150') is None  # bare fraction is not supported


@pytest.mark.parametrize("size,rendered", [(6.0, "6"), (1.5, "1-1/2"), (0.75, "3/4"), (10.0, "10")])
def test_format_size_uses_drawing_fractions(size, rendered):
    assert format_size(size) == rendered


def test_line_number_formatting_round_trips():
    number = format_line_number(1.5, "ST", 1310, "CS300")
    assert number == '1-1/2"-ST-1310-CS300'
    assert parse_line_number(number).size_in == 1.5


def test_a_well_formed_line_number_validates_silently():
    issues = validate_line_number('6"-PL-1201-CS150')
    assert issues.ok and not issues.warnings


def test_missing_spec_warns_because_the_line_cannot_be_procured():
    issues = validate_line_number('6"-PL-1201')
    assert issues.ok  # structurally valid
    assert any("spec" in message for message in issues.warnings)


def test_unknown_service_and_nonstandard_size_warn():
    assert validate_line_number('6"-ZZ-1201-CS150').warnings
    assert validate_line_number('5"-PL-1201-CS150').warnings


def test_malformed_line_numbers_are_errors():
    assert validate_line_number("6-PL-1201-CS150").errors
    assert validate_line_number("PL-1201").errors
