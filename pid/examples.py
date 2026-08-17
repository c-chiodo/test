"""A worked P&ID, built by hand.

An edible-oil bleaching unit: degummed oil is heated, contacted with bleaching
earth under vacuum, then filtered. It is here for three reasons — it gives the
tests a realistic drawing that needs no API key, it documents the model API by
example, and ``pid example`` emits it so the renderer and exporters can be tried
out before spending a token.

The drawing is deliberately *complete*: it passes the rule engine cleanly, so a
new rule that fires on it is either a real find or a false positive worth
knowing about.
"""

from __future__ import annotations

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


def _boundary(tag: str, name: str) -> Equipment:
    return Equipment(tag=tag, name=name, kind="boundary", ports=[Port(name="N1", kind="inlet")])


def bleaching_unit() -> PIDModel:
    """A 1200-series P&ID for an oil bleaching unit."""
    equipment = [
        _boundary("OSBL-1201", "From degummed oil storage"),
        _boundary("OSBL-1202", "To deodoriser"),
        _boundary("OSBL-1203", "LP steam supply"),
        _boundary("OSBL-1204", "Condensate return"),
        _boundary("OSBL-1205", "Nitrogen supply"),
        _boundary("OSBL-1206", "To vent header"),
        _boundary("OSBL-1207", "Spent earth to disposal"),
        Equipment(
            tag="TK-1201",
            name="Degummed oil day tank",
            kind="tank",
            capacity="12,000 gal",
            material="Carbon steel, epoxy lined",
            design_pressure_psig=15.0,
            design_temp_f=200.0,
            insulated=True,
            ports=[
                Port(name="N1", kind="inlet", description="Degummed oil feed", size_in=8.0),
                Port(name="N2", kind="outlet", description="Suction header", size_in=6.0),
                Port(name="N3", kind="utility_in", description="Nitrogen blanket", size_in=1.5),
                Port(name="N4", kind="relief", description="Relief and vent", size_in=2.0),
            ],
        ),
        Equipment(
            tag="P-1201A",
            name="Bleacher feed pump",
            kind="pump",
            capacity="150 gpm @ 140 ft TDH",
            material="316L SS",
            design_pressure_psig=150.0,
            design_temp_f=250.0,
            spared_by="P-1201B",
            ports=[
                Port(name="suction", kind="inlet", size_in=6.0),
                Port(name="discharge", kind="outlet", size_in=4.0),
            ],
        ),
        Equipment(
            tag="P-1201B",
            name="Bleacher feed pump, installed spare",
            kind="pump",
            capacity="150 gpm @ 140 ft TDH",
            material="316L SS",
            design_pressure_psig=150.0,
            design_temp_f=250.0,
            ports=[
                Port(name="suction", kind="inlet", size_in=6.0),
                Port(name="discharge", kind="outlet", size_in=4.0),
            ],
        ),
        Equipment(
            tag="E-1201",
            name="Bleacher feed heater",
            kind="heat_exchanger",
            capacity="2.1 MMBtu/h, shell and tube",
            material="316L SS tubes, CS shell",
            design_pressure_psig=150.0,
            design_temp_f=350.0,
            insulated=True,
            ports=[
                Port(name="P1", kind="inlet", description="Oil in, tube side", size_in=4.0),
                Port(name="P2", kind="outlet", description="Oil out, tube side", size_in=4.0),
                Port(name="U1", kind="utility_in", description="Steam in, shell side", size_in=2.0),
                Port(name="U2", kind="utility_out", description="Condensate out", size_in=1.0),
                Port(name="R1", kind="relief", description="Tube-side relief", size_in=1.0),
            ],
        ),
        Equipment(
            tag="V-1201",
            name="Bleacher",
            kind="vessel",
            capacity="3,000 gal, agitated, 60 mmHg abs",
            material="316L SS",
            design_pressure_psig=50.0,
            design_temp_f=250.0,
            insulated=True,
            ports=[
                Port(name="N1", kind="inlet", description="Heated oil in", size_in=4.0),
                Port(name="N2", kind="outlet", description="Slurry out", size_in=4.0),
                Port(name="N3", kind="relief", description="Relief", size_in=2.0),
            ],
            notes="Bleaching earth dosed via screw feeder on adjacent drawing",
        ),
        Equipment(
            tag="P-1202",
            name="Bleacher discharge pump",
            kind="pump",
            capacity="150 gpm @ 90 ft TDH, slurry duty",
            material="316L SS",
            design_pressure_psig=150.0,
            design_temp_f=250.0,
            ports=[
                Port(name="suction", kind="inlet", size_in=4.0),
                Port(name="discharge", kind="outlet", size_in=3.0),
            ],
        ),
        Equipment(
            tag="F-1201",
            name="Bleaching earth filter",
            kind="filter",
            capacity="900 ft2 vertical leaf",
            material="316L SS",
            design_pressure_psig=90.0,
            design_temp_f=250.0,
            ports=[
                Port(name="N1", kind="inlet", description="Slurry in", size_in=3.0),
                Port(name="N2", kind="outlet", description="Filtered oil out", size_in=3.0),
                Port(name="N3", kind="drain", description="Spent earth discharge", size_in=3.0),
            ],
        ),
        Equipment(
            tag="F-1202",
            name="Polish filter",
            kind="filter",
            capacity="120 ft2 cartridge",
            material="316L SS",
            design_pressure_psig=90.0,
            design_temp_f=250.0,
            ports=[
                Port(name="N1", kind="inlet", size_in=3.0),
                Port(name="N2", kind="outlet", size_in=3.0),
            ],
        ),
    ]

    streams = [
        Stream(
            number='8"-OIL-1201-CS150',
            source=Endpoint(equipment="OSBL-1201"),
            destination=Endpoint(equipment="TK-1201", port="N1"),
            service="OIL",
            description="Degummed oil to day tank",
            size_in=8.0,
            spec="CS150",
            phase="liquid",
            inline=[InlineItem(type="gate_valve", notes="Tie-in isolation")],
        ),
        Stream(
            number='6"-OIL-1202-CS150',
            source=Endpoint(equipment="TK-1201", port="N2"),
            destination=Endpoint(equipment="P-1201A", port="suction"),
            service="OIL",
            description="Day tank to feed pump A suction",
            size_in=6.0,
            spec="CS150",
            phase="liquid",
            insulated=True,
            inline=[
                InlineItem(type="gate_valve", notes="Suction isolation, locked open"),
                InlineItem(type="strainer", notes="Y-type, 40 mesh"),
            ],
        ),
        Stream(
            number='6"-OIL-1203-CS150',
            source=Endpoint(equipment="TK-1201", port="N2"),
            destination=Endpoint(equipment="P-1201B", port="suction"),
            service="OIL",
            description="Day tank to feed pump B suction",
            size_in=6.0,
            spec="CS150",
            phase="liquid",
            insulated=True,
            inline=[
                InlineItem(type="gate_valve", notes="Suction isolation, locked open"),
                InlineItem(type="strainer", notes="Y-type, 40 mesh"),
            ],
        ),
        Stream(
            number='4"-OIL-1204-CS150',
            source=Endpoint(equipment="P-1201A", port="discharge"),
            destination=Endpoint(equipment="E-1201", port="P1"),
            service="OIL",
            description="Feed pump A discharge to heater",
            size_in=4.0,
            spec="CS150",
            phase="liquid",
            design_flow="150 gpm",
            insulated=True,
            inline=[
                InlineItem(type="check_valve"),
                InlineItem(type="gate_valve", notes="Discharge isolation"),
                InlineItem(type="orifice", tag="FE-1201", notes="Square-edged, 0-250 gpm"),
                InlineItem(
                    type="control_valve",
                    tag="FV-1201",
                    fail_position="fail_closed",
                    notes="Fails closed to stop feed on air failure",
                ),
            ],
        ),
        Stream(
            number='4"-OIL-1205-CS150',
            source=Endpoint(equipment="P-1201B", port="discharge"),
            destination=Endpoint(equipment="E-1201", port="P1"),
            service="OIL",
            description="Feed pump B discharge to heater",
            size_in=4.0,
            spec="CS150",
            phase="liquid",
            insulated=True,
            inline=[
                InlineItem(type="check_valve"),
                InlineItem(type="gate_valve", notes="Discharge isolation"),
            ],
        ),
        Stream(
            number='4"-OIL-1206-CS150J',
            source=Endpoint(equipment="E-1201", port="P2"),
            destination=Endpoint(equipment="V-1201", port="N1"),
            service="OIL",
            description="Heated oil to bleacher",
            size_in=4.0,
            spec="CS150J",
            phase="liquid",
            design_flow="150 gpm at 220 degF",
            insulated=True,
            traced=True,
        ),
        Stream(
            number='2"-ST-1207-CS300',
            source=Endpoint(equipment="OSBL-1203"),
            destination=Endpoint(equipment="E-1201", port="U1"),
            service="ST",
            description="LP steam to feed heater",
            size_in=2.0,
            spec="CS300",
            phase="steam",
            insulated=True,
            inline=[
                InlineItem(type="gate_valve", notes="Steam isolation"),
                InlineItem(
                    type="control_valve",
                    tag="TV-1202",
                    fail_position="fail_closed",
                    notes="Fails closed to cut heat input on air failure",
                ),
            ],
        ),
        Stream(
            number='1"-SC-1208-CS300',
            source=Endpoint(equipment="E-1201", port="U2"),
            destination=Endpoint(equipment="OSBL-1204"),
            service="SC",
            description="Condensate to return header",
            size_in=1.0,
            spec="CS300",
            phase="liquid",
            insulated=True,
            inline=[InlineItem(type="steam_trap"), InlineItem(type="check_valve")],
        ),
        Stream(
            number='4"-PS-1209-CS150J',
            source=Endpoint(equipment="V-1201", port="N2"),
            destination=Endpoint(equipment="P-1202", port="suction"),
            service="PS",
            description="Bleacher slurry to discharge pump",
            size_in=4.0,
            spec="CS150J",
            phase="slurry",
            insulated=True,
            traced=True,
            notes="Slope 1:100 to pump, no pockets",
            inline=[InlineItem(type="gate_valve", notes="Suction isolation")],
        ),
        Stream(
            number='3"-PS-1210-CS150J',
            source=Endpoint(equipment="P-1202", port="discharge"),
            destination=Endpoint(equipment="F-1201", port="N1"),
            service="PS",
            description="Slurry to bleaching earth filter",
            size_in=3.0,
            spec="CS150J",
            phase="slurry",
            insulated=True,
            traced=True,
            inline=[
                InlineItem(type="check_valve"),
                InlineItem(type="gate_valve", notes="Discharge isolation"),
                InlineItem(
                    type="control_valve",
                    tag="LV-1204",
                    fail_position="fail_closed",
                    notes="Throttles on bleacher level; fails closed to hold inventory",
                ),
            ],
        ),
        Stream(
            number='3"-OIL-1211-CS150',
            source=Endpoint(equipment="F-1201", port="N2"),
            destination=Endpoint(equipment="F-1202", port="N1"),
            service="OIL",
            description="Filtered oil to polish filter",
            size_in=3.0,
            spec="CS150",
            phase="liquid",
            insulated=True,
            inline=[InlineItem(type="gate_valve")],
        ),
        Stream(
            number='3"-OIL-1212-CS150',
            source=Endpoint(equipment="F-1202", port="N2"),
            destination=Endpoint(equipment="OSBL-1202"),
            service="OIL",
            description="Bleached oil to deodoriser",
            size_in=3.0,
            spec="CS150",
            phase="liquid",
            insulated=True,
            inline=[
                InlineItem(type="gate_valve"),
                InlineItem(type="sample_point", notes="Colour and peroxide value"),
            ],
        ),
        Stream(
            number='1-1/2"-N2-1213-CS150',
            source=Endpoint(equipment="OSBL-1205"),
            destination=Endpoint(equipment="TK-1201", port="N3"),
            service="N2",
            description="Nitrogen blanket to day tank",
            size_in=1.5,
            spec="CS150",
            phase="gas",
            inline=[
                InlineItem(type="gate_valve"),
                InlineItem(
                    type="control_valve",
                    tag="PV-1203",
                    fail_position="fail_closed",
                    notes="Fails closed so a stuck valve cannot over-pressure the tank",
                ),
                InlineItem(type="check_valve", notes="Prevents oil vapour into the N2 header"),
            ],
        ),
        Stream(
            number='2"-VE-1214-CS150',
            source=Endpoint(equipment="V-1201", port="N3"),
            destination=Endpoint(equipment="OSBL-1206"),
            service="VE",
            description="Bleacher relief to vent header",
            size_in=2.0,
            spec="CS150",
            phase="vapor",
            inline=[
                InlineItem(
                    type="relief_valve",
                    tag="PSV-1201",
                    set_pressure_psig=45.0,
                    notes="Blocked-outlet case, 2 in x 3 in, set below the 50 psig MAWP",
                )
            ],
        ),
        Stream(
            number='2"-VE-1215-CS150',
            source=Endpoint(equipment="TK-1201", port="N4"),
            destination=Endpoint(equipment="OSBL-1206"),
            service="VE",
            description="Day tank relief and vent",
            size_in=2.0,
            spec="CS150",
            phase="vapor",
            inline=[
                InlineItem(
                    type="relief_valve",
                    tag="PSV-1202",
                    set_pressure_psig=12.0,
                    notes="Nitrogen regulator failure case",
                )
            ],
        ),
        Stream(
            number='1"-VE-1217-CS150',
            source=Endpoint(equipment="E-1201", port="R1"),
            destination=Endpoint(equipment="OSBL-1206"),
            service="VE",
            description="Heater tube-side thermal relief",
            size_in=1.0,
            spec="CS150",
            phase="liquid",
            inline=[
                InlineItem(
                    type="relief_valve",
                    tag="PSV-1203",
                    set_pressure_psig=140.0,
                    notes="Tube side blocked in against LP steam; thermal expansion case",
                )
            ],
        ),
        Stream(
            number='3"-SL-1216-CS150',
            source=Endpoint(equipment="F-1201", port="N3"),
            destination=Endpoint(equipment="OSBL-1207"),
            service="SL",
            description="Spent bleaching earth to disposal bin",
            size_in=3.0,
            spec="CS150",
            phase="solid",
            inline=[
                InlineItem(
                    type="gate_valve",
                    normally_closed=True,
                    notes="Opened only for filter discharge",
                )
            ],
        ),
    ]

    instruments = [
        # Loop 1201 — feed flow control
        Instrument(
            tag="FT-1201",
            description="Bleacher feed flow",
            attached_to='4"-OIL-1204-CS150',
            location="field",
            loop="1201",
            range_low=0.0,
            range_high=250.0,
            units="gpm",
        ),
        Instrument(
            tag="FIC-1201",
            description="Bleacher feed flow controller",
            attached_to='4"-OIL-1204-CS150',
            location="shared_display",
            loop="1201",
            units="gpm",
            setpoint="150 gpm",
        ),
        # Loop 1202 — heater outlet temperature control
        Instrument(
            tag="TE-1202",
            description="Heater outlet oil temperature element",
            attached_to='4"-OIL-1206-CS150J',
            location="field",
            loop="1202",
            notes="316L thermowell",
        ),
        Instrument(
            tag="TT-1202",
            description="Heater outlet oil temperature",
            attached_to='4"-OIL-1206-CS150J',
            location="field",
            loop="1202",
            range_low=100.0,
            range_high=300.0,
            units="degF",
        ),
        Instrument(
            tag="TIC-1202",
            description="Heater outlet temperature controller",
            attached_to='4"-OIL-1206-CS150J',
            location="shared_display",
            loop="1202",
            units="degF",
            setpoint="220 degF",
        ),
        # Loop 1203 — day tank blanket pressure control
        Instrument(
            tag="PT-1203",
            description="Day tank vapour space pressure",
            attached_to="TK-1201",
            location="field",
            loop="1203",
            range_low=-1.0,
            range_high=15.0,
            units="psig",
        ),
        Instrument(
            tag="PIC-1203",
            description="Day tank blanket pressure controller",
            attached_to="TK-1201",
            location="shared_display",
            loop="1203",
            units="psig",
            setpoint="0.5 psig",
        ),
        # Loop 1204 — bleacher level control
        Instrument(
            tag="LT-1204",
            description="Bleacher level",
            attached_to="V-1201",
            location="field",
            loop="1204",
            range_low=0.0,
            range_high=100.0,
            units="%",
            notes="Guided wave radar, slurry service",
        ),
        Instrument(
            tag="LIC-1204",
            description="Bleacher level controller",
            attached_to="V-1201",
            location="shared_display",
            loop="1204",
            units="%",
            setpoint="60 %",
        ),
        # Indication and protection
        Instrument(
            tag="LT-1205",
            description="Day tank level",
            attached_to="TK-1201",
            location="shared_display",
            range_low=0.0,
            range_high=100.0,
            units="%",
        ),
        Instrument(
            tag="LSLL-1206",
            description="Day tank low-low level, feed pump protection",
            attached_to="TK-1201",
            location="plc",
            units="%",
            setpoint="8 %",
        ),
        Instrument(
            tag="PI-1207",
            description="Feed pump A discharge pressure",
            attached_to='4"-OIL-1204-CS150',
            location="field",
            units="psig",
        ),
        Instrument(
            tag="PI-1208",
            description="Feed pump B discharge pressure",
            attached_to='4"-OIL-1205-CS150',
            location="field",
            units="psig",
        ),
        Instrument(
            tag="PI-1209",
            description="Bleacher operating pressure",
            attached_to="V-1201",
            location="field",
            units="mmHg abs",
        ),
        Instrument(
            tag="TI-1210",
            description="Bleacher contact temperature",
            attached_to="V-1201",
            location="shared_display",
            units="degF",
        ),
        Instrument(
            tag="PDI-1211",
            description="Earth filter differential pressure",
            attached_to="F-1201",
            location="shared_display",
            range_low=0.0,
            range_high=60.0,
            units="psid",
            notes="Rising dP indicates filter cake build-up",
        ),
        Instrument(
            tag="TSH-1213",
            description="Heater outlet high temperature trip",
            attached_to='4"-OIL-1206-CS150J',
            location="plc",
            units="degF",
            setpoint="245 degF",
        ),
        Instrument(
            tag="PSH-1214",
            description="Bleacher high pressure trip",
            attached_to="V-1201",
            location="plc",
            units="psig",
            setpoint="35 psig",
        ),
        Instrument(
            tag="PI-1215",
            description="Discharge pump pressure",
            attached_to='3"-PS-1210-CS150J',
            location="field",
            units="psig",
        ),
    ]

    loops = [
        ControlLoop(
            number="1201",
            description="Bleacher feed flow control",
            controlled_variable="Oil feed rate to the bleacher",
            measurement_tags=["FE-1201", "FT-1201"],
            controller_tag="FIC-1201",
            final_element_tag="FV-1201",
            action="reverse",
            setpoint="150 gpm",
        ),
        ControlLoop(
            number="1202",
            description="Heater outlet temperature control",
            controlled_variable="Oil temperature entering the bleacher",
            measurement_tags=["TE-1202", "TT-1202"],
            controller_tag="TIC-1202",
            final_element_tag="TV-1202",
            action="reverse",
            setpoint="220 degF",
            notes="Steam throttled to hold bleaching contact temperature",
        ),
        ControlLoop(
            number="1203",
            description="Day tank nitrogen blanket pressure control",
            controlled_variable="Day tank vapour space pressure",
            measurement_tags=["PT-1203"],
            controller_tag="PIC-1203",
            final_element_tag="PV-1203",
            action="reverse",
            setpoint="0.5 psig",
        ),
        ControlLoop(
            number="1204",
            description="Bleacher level control",
            controlled_variable="Bleacher working level",
            measurement_tags=["LT-1204"],
            controller_tag="LIC-1204",
            final_element_tag="LV-1204",
            action="direct",
            setpoint="60 %",
            notes="Discharge throttled to hold contact time",
        ),
    ]

    interlocks = [
        Interlock(
            tag="I-1201",
            description="Day tank low-low level stops the feed pumps to prevent dry running",
            initiators=["LSLL-1206"],
            actions=["Stop P-1201A", "Stop P-1201B", "Close FV-1201"],
            trip_setpoint="8 % level",
            reset="manual",
        ),
        Interlock(
            tag="I-1202",
            description="Heater outlet high temperature cuts steam to protect oil quality",
            initiators=["TSH-1213"],
            actions=["Close TV-1202"],
            trip_setpoint="245 degF",
            reset="automatic",
        ),
        Interlock(
            tag="I-1203",
            description="Bleacher high pressure stops feed and heat input",
            initiators=["PSH-1214"],
            actions=["Close FV-1201", "Close TV-1202", "Stop P-1201A", "Stop P-1201B"],
            trip_setpoint="35 psig",
            reset="manual",
            sil="SIL 1",
        ),
    ]

    return PIDModel(
        project="Refinery — Oil Processing",
        drawing_number="PID-1200-001",
        title="Oil Bleaching Unit",
        revision="A",
        description=(
            "Degummed oil is drawn from the day tank by the bleacher feed pumps, heated "
            "against LP steam, and contacted with bleaching earth under vacuum in the "
            "bleacher. The slurry is pumped to the earth filter and a polish filter "
            "before going to deodorising."
        ),
        equipment=equipment,
        streams=streams,
        instruments=instruments,
        loops=loops,
        interlocks=interlocks,
        assumptions=[
            "Bleaching earth dosing and the vacuum system are on adjacent drawings",
            "Day tank design pressure taken as 15 psig for a nitrogen-blanketed atmospheric tank",
        ],
        notes=[
            "All oil lines insulated and heat traced to maintain 180 degF minimum",
            "Slurry lines sloped 1:100 to the pump suction with no pockets",
            "Spent earth discharge valve is opened only for filter discharge",
        ],
    )


EXAMPLES = {"bleaching": bleaching_unit}


def load_example(name: str) -> PIDModel:
    if name not in EXAMPLES:
        raise KeyError(f"no example {name!r}; available: {', '.join(sorted(EXAMPLES))}")
    return EXAMPLES[name]()
