"""ISA-5.1 / ISA-5.2 reference data for P&ID tagging and symbology.

Encoded from ANSI/ISA-5.1-2009 "Instrumentation Symbols and Identification".
Everything here is a lookup table rather than logic so the agents can be told
the conventions in a prompt and the validator can check them independently —
the LLM proposes tags, this module decides whether they are legal.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ISA-5.1 Table 1: instrument identification letters
# ---------------------------------------------------------------------------

# First letter — the measured or initiating variable.
MEASURED_VARIABLES: dict[str, str] = {
    "A": "Analysis",
    "B": "Burner / combustion",
    "C": "User's choice (conductivity by convention)",
    "D": "Density / specific gravity",
    "E": "Voltage",
    "F": "Flow rate",
    "G": "Gauging / dimensional",
    "H": "Hand (manual initiation)",
    "I": "Current",
    "J": "Power",
    "K": "Time / schedule",
    "L": "Level",
    "M": "Moisture / humidity",
    "N": "User's choice",
    "O": "User's choice",
    "P": "Pressure",
    "Q": "Quantity (totalized)",
    "R": "Radiation",
    "S": "Speed / frequency",
    "T": "Temperature",
    "U": "Multivariable",
    "V": "Vibration / mechanical analysis",
    "W": "Weight / force",
    "X": "Unclassified",
    "Y": "Event / state / presence",
    "Z": "Position / dimension",
}

# Succeeding letters — modifiers, readout functions and output functions.
SUCCEEDING_LETTERS: dict[str, str] = {
    "A": "Alarm",
    "C": "Control",
    "D": "Differential (modifier)",
    "E": "Sensor / primary element",
    "F": "Ratio (modifier)",
    "G": "Glass / gauge / viewing device",
    "H": "High (modifier)",
    "I": "Indicate",
    "K": "Control station",
    "L": "Low (modifier)",
    "M": "Middle / intermediate (modifier)",
    "N": "User's choice",
    "O": "Orifice / restriction",
    "P": "Test point / connection",
    "Q": "Totalize / integrate",
    "R": "Record",
    "S": "Switch / safety (modifier)",
    "T": "Transmit",
    "U": "Multifunction",
    "V": "Valve / damper / louver",
    "W": "Well / probe",
    "X": "Unclassified",
    "Y": "Relay / compute / convert",
    "Z": "Driver / actuator / final control element",
}

# Modifier letters may only appear immediately after the letter they modify.
MODIFIERS = frozenset("DFHLMS")

# A final control element is any device the controller can move.
FINAL_ELEMENT_LETTERS = frozenset("VZ")

# Letters that make an instrument a sensing device rather than an output device.
SENSING_LETTERS = frozenset("EWG")


# ---------------------------------------------------------------------------
# Equipment tag prefixes (no single governing standard; these follow the
# prefixes most commonly used in oils/fats and feed processing plants)
# ---------------------------------------------------------------------------

EQUIPMENT_PREFIXES: dict[str, str] = {
    "pump": "P",
    "tank": "TK",
    "vessel": "V",
    "reactor": "R",
    "column": "C",
    "heat_exchanger": "E",
    "filter": "F",
    "compressor": "K",
    "blower": "B",
    "centrifuge": "CF",
    "dryer": "DR",
    "mixer": "M",
    "conveyor": "CV",
    "scale": "WT",
    "separator": "S",
    "boundary": "OSBL",
}

# Equipment that holds inventory and therefore needs level measurement.
INVENTORY_EQUIPMENT = frozenset(
    {"tank", "vessel", "reactor", "column", "separator", "dryer"}
)

# Equipment that can be over-pressured and therefore needs a relief device.
# Heat exchangers are included because a blocked-in cold side with a hot
# utility on the other side is a classic over-pressure case (API 521 §4.4.13).
RELIEF_REQUIRED_EQUIPMENT = frozenset(
    {"vessel", "reactor", "column", "heat_exchanger", "separator"}
)

# Rotating equipment needs isolation, and centrifugal machines need a
# non-return device on discharge to stop reverse flow on trip.
ROTATING_EQUIPMENT = frozenset(
    {"pump", "compressor", "blower", "centrifuge", "mixer", "dryer", "conveyor"}
)


# ---------------------------------------------------------------------------
# Inline component types permitted on a line
# ---------------------------------------------------------------------------

INLINE_TYPES: dict[str, str] = {
    "gate_valve": "Gate valve (block / isolation)",
    "globe_valve": "Globe valve (throttling)",
    "ball_valve": "Ball valve (block)",
    "butterfly_valve": "Butterfly valve",
    "check_valve": "Check valve (non-return)",
    "control_valve": "Control valve (automatic final element)",
    "relief_valve": "Pressure relief valve",
    "rupture_disc": "Rupture disc",
    "three_way_valve": "Three-way valve",
    "orifice": "Restriction orifice / flow element",
    "strainer": "Strainer",
    "sight_glass": "Sight glass",
    "reducer": "Reducer / expander",
    "spectacle_blind": "Spectacle blind",
    "sample_point": "Sample connection",
    "steam_trap": "Steam trap",
    "flame_arrestor": "Flame arrestor",
}

BLOCK_VALVE_TYPES = frozenset(
    {"gate_valve", "ball_valve", "butterfly_valve", "globe_valve"}
)
RELIEF_DEVICE_TYPES = frozenset({"relief_valve", "rupture_disc"})


# ---------------------------------------------------------------------------
# Line service codes (the middle field of a line number)
# ---------------------------------------------------------------------------

SERVICE_CODES: dict[str, str] = {
    "P": "Process",
    "PL": "Process liquid",
    "PG": "Process gas",
    "PS": "Process slurry",
    "OIL": "Crude / refined oil",
    "WO": "Wash oil",
    "FA": "Fatty acid",
    "CW": "Cooling water supply",
    "CWR": "Cooling water return",
    "SW": "Soft / service water",
    "PW": "Process water",
    "ST": "Steam",
    "SC": "Steam condensate",
    "IA": "Instrument air",
    "PA": "Plant air",
    "N2": "Nitrogen",
    "NG": "Natural gas",
    "CA": "Caustic",
    "AC": "Acid",
    "VE": "Vent",
    "DR": "Drain",
    "SL": "Sludge",
}

# Piping specification codes: material-rating shorthand used as the tail of a
# line number, e.g. the "CS150" in 6"-PL-1201-CS150.
PIPE_SPECS: dict[str, str] = {
    "CS150": "Carbon steel, ASME Class 150",
    "CS300": "Carbon steel, ASME Class 300",
    "SS150": "316L stainless, ASME Class 150",
    "SS300": "316L stainless, ASME Class 300",
    "HDPE": "High-density polyethylene",
    "PVC": "PVC",
    "CS150J": "Carbon steel, Class 150, steam jacketed",
    "SS150J": "316L stainless, Class 150, steam jacketed",
}

# Nominal pipe sizes in inches that a line may legally be drawn as.
NOMINAL_SIZES: tuple[float, ...] = (
    0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0, 10.0,
    12.0, 14.0, 16.0, 18.0, 20.0, 24.0,
)


# ---------------------------------------------------------------------------
# Signal / instrument location conventions (ISA-5.1 Table 4 & 5)
# ---------------------------------------------------------------------------

SIGNAL_TYPES: dict[str, str] = {
    "electric": "Electric / electronic (4-20 mA)",
    "pneumatic": "Pneumatic",
    "data": "Shared digital data link",
    "capillary": "Filled thermal / capillary",
    "hydraulic": "Hydraulic",
    "software": "Internal software / data link",
}

# Where the instrument's readout lives, which drives the bubble symbol.
INSTRUMENT_LOCATIONS: dict[str, str] = {
    "field": "Field mounted, locally accessible",
    "field_aux": "Field auxiliary panel",
    "shared_display": "Shared display / shared control (DCS)",
    "shared_aux": "Shared display, auxiliary location",
    "computer": "Computer function",
    "plc": "Programmable logic controller",
    "inaccessible": "Field mounted, not normally accessible",
}
