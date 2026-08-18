"""Dynamics GP output: eConnect receivings XML.

GP's supported integration surface is eConnect, which ships with every GP
install. A purchasing shipment receipt is a ``POPReceivingsType`` document
(header ``taPopRcptHdrInsert`` + one ``taPopRcptLineInsert`` per line).
The agent writes one XML file per receipt into an outbox directory; a tiny
on-prem bridge (PowerShell or the eConnect .NET assembly — see
docs/AP_AGENT.md) submits each file and archives it. This keeps the agent
itself off the GP server and makes every posting inspectable and
replayable.

A CSV fallback is included for shops that prefer SmartConnect/Integration
Manager as the last hop.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import MatchedLine

POPTYPE_SHIPMENT = 1          # Receivings Transaction Entry: Shipment
DEFAULT_BATCH = "APAGENT"


def build_receipt_xml(receipt_number: str,
                      po_number: str,
                      vendor_id: str,
                      lines: list[MatchedLine],
                      receipt_date: date | None = None,
                      vendor_doc_number: str = "",
                      batch: str = DEFAULT_BATCH) -> str:
    """Render one POP shipment receipt as an eConnect XML document."""
    receipt_date = receipt_date or date.today()

    root = ET.Element("eConnect")
    doc = ET.SubElement(root, "POPReceivingsType")

    items = ET.SubElement(doc, "taPopRcptLineInsert_Items")
    for line in lines:
        el = ET.SubElement(items, "taPopRcptLineInsert")
        _add(el, "POPRCTNM", receipt_number)
        _add(el, "PONUMBER", po_number)
        _add(el, "ITEMNMBR", line.gp_item)
        if line.vendor_item:
            _add(el, "VNDITNUM", line.vendor_item)
        if line.uom:
            _add(el, "UOFM", line.uom)
        _add(el, "UNITCOST", f"{line.unit_cost:.5f}")
        _add(el, "QTYSHPPD", _qty(line.quantity))
        _add(el, "AUTOCOST", "0")  # keep the document's cost, don't re-default

    hdr = ET.SubElement(doc, "taPopRcptHdrInsert")
    _add(hdr, "POPRCTNM", receipt_number)
    _add(hdr, "POPTYPE", str(POPTYPE_SHIPMENT))
    _add(hdr, "VENDORID", vendor_id)
    _add(hdr, "receiptdate", receipt_date.strftime("%m/%d/%Y"))
    _add(hdr, "BACHNUMB", batch)
    if vendor_doc_number:
        _add(hdr, "VNDDOCNM", vendor_doc_number)

    buf = io.BytesIO()
    ET.indent(root)
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue().decode("utf-8")


def _add(parent: ET.Element, tag: str, text: str) -> None:
    ET.SubElement(parent, tag).text = text


def _qty(q: float) -> str:
    return f"{q:g}"


def write_outbox(xml: str, receipt_number: str, outbox: str | Path) -> Path:
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{receipt_number}.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def write_csv_fallback(receipt_number: str,
                       po_number: str,
                       vendor_id: str,
                       lines: list[MatchedLine],
                       outbox: str | Path,
                       receipt_date: date | None = None,
                       vendor_doc_number: str = "") -> Path:
    """Flat file for SmartConnect / Integration Manager imports."""
    receipt_date = receipt_date or date.today()
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{receipt_number}.csv"
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["receipt_number", "receipt_date", "po_number", "vendor_id",
                         "vendor_doc_number", "gp_item", "vendor_item", "uom",
                         "quantity", "unit_cost"])
        for line in lines:
            writer.writerow([receipt_number, receipt_date.isoformat(), po_number,
                             vendor_id, vendor_doc_number, line.gp_item,
                             line.vendor_item, line.uom, _qty(line.quantity),
                             f"{line.unit_cost:.5f}"])
    return path
