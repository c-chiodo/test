"""Validation of an extracted PO against Dynamics GP master data.

The agent never posts blind: every extraction is reconciled against the
open purchase order in GP (vendor exists, PO exists and is theirs, items
are on the PO, quantity fits what is still to be received, price within
tolerance). Anything that fails becomes an ``Issue``; error-level issues
block auto-post.

Master data comes through the ``MasterData`` protocol with two
implementations:

* ``CsvMasterData`` — snapshot CSVs exported from GP (vendors + open PO
  lines). Zero-footprint way to start, and what the tests use.
* ``SqlMasterData`` — live read-only queries against the GP company
  database over ODBC (POP10100/POP10110, PM00200, IV00101). Optional;
  requires ``pyodbc`` and a read-only SQL login.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional, Protocol

from pydantic import BaseModel

from .models import ExtractedPO, Issue, MatchedLine, Severity
from .store import VendorProfile


class Vendor(BaseModel):
    vendor_id: str
    name: str
    domain: str = ""


class OpenPOLine(BaseModel):
    po_number: str
    vendor_id: str
    gp_item: str
    vendor_item: str = ""
    uom: str = ""
    unit_cost: float = 0.0
    qty_ordered: float = 0.0
    qty_remaining: float = 0.0


class MasterData(Protocol):
    def find_vendor(self, *, domain: str = "", name: str = "") -> Optional[Vendor]: ...
    def open_po_lines(self, po_number: str) -> list[OpenPOLine]: ...


class CsvMasterData:
    """vendors.csv: vendor_id,name,domain
    open_po_lines.csv: po_number,vendor_id,gp_item,vendor_item,uom,unit_cost,qty_ordered,qty_remaining
    """

    def __init__(self, directory: str | Path):
        directory = Path(directory)
        with open(directory / "vendors.csv", newline="") as fh:
            self.vendors = [Vendor(**row) for row in csv.DictReader(fh)]
        with open(directory / "open_po_lines.csv", newline="") as fh:
            self.po_lines = [OpenPOLine(**row) for row in csv.DictReader(fh)]

    def find_vendor(self, *, domain: str = "", name: str = "") -> Optional[Vendor]:
        domain = domain.lower()
        name_l = name.lower()
        for v in self.vendors:
            if domain and v.domain.lower() == domain:
                return v
        for v in self.vendors:
            if name_l and (v.name.lower() == name_l
                           or name_l in v.name.lower()
                           or v.name.lower() in name_l):
                return v
        return None

    def open_po_lines(self, po_number: str) -> list[OpenPOLine]:
        return [ln for ln in self.po_lines
                if ln.po_number.lower() == po_number.lower()]


class SqlMasterData:
    """Live read-only lookups against the GP company database (e.g. TWO)."""

    _VENDOR_SQL = (
        "SELECT RTRIM(VENDORID) vendor_id, RTRIM(VENDNAME) name "
        "FROM PM00200 WHERE VENDSTTS = 1")
    _PO_SQL = (
        "SELECT RTRIM(h.PONUMBER) po_number, RTRIM(h.VENDORID) vendor_id, "
        "RTRIM(d.ITEMNMBR) gp_item, RTRIM(d.VNDITNUM) vendor_item, "
        "RTRIM(d.UOFM) uom, d.UNITCOST unit_cost, d.QTYORDER qty_ordered, "
        "d.QTYORDER - d.QTYSHPPD qty_remaining "
        "FROM POP10100 h JOIN POP10110 d ON d.PONUMBER = h.PONUMBER "
        "WHERE h.PONUMBER = ? AND h.POSTATUS IN (2, 3, 4)")  # released..change

    def __init__(self, dsn: str):
        import pyodbc  # optional dependency
        self._conn = pyodbc.connect(dsn, readonly=True)

    def find_vendor(self, *, domain: str = "", name: str = "") -> Optional[Vendor]:
        if not name:
            return None
        cur = self._conn.cursor()
        cur.execute(self._VENDOR_SQL)
        name_l = name.lower()
        for vendor_id, vname in cur.fetchall():
            if vname.lower() == name_l or name_l in vname.lower():
                return Vendor(vendor_id=vendor_id, name=vname)
        return None

    def open_po_lines(self, po_number: str) -> list[OpenPOLine]:
        cur = self._conn.cursor()
        cur.execute(self._PO_SQL, po_number)
        cols = ["po_number", "vendor_id", "gp_item", "vendor_item",
                "uom", "unit_cost", "qty_ordered", "qty_remaining"]
        return [OpenPOLine(**dict(zip(cols, row))) for row in cur.fetchall()]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

PRICE_WARN_TOLERANCE = 0.02   # >2% off PO price -> warning
PRICE_ERROR_TOLERANCE = 0.10  # >10% off PO price -> error


def validate(extracted: ExtractedPO,
             profile: VendorProfile | None,
             master: MasterData,
             sender_domain: str = "") -> tuple[list[MatchedLine], list[Issue], Optional[Vendor]]:
    """Reconcile an extraction against GP. Returns (matched lines, issues, vendor)."""
    issues: list[Issue] = []
    matched: list[MatchedLine] = []

    vendor = master.find_vendor(domain=sender_domain, name=extracted.vendor_name)
    if profile and profile.gp_vendor_id and vendor is None:
        vendor = Vendor(vendor_id=profile.gp_vendor_id,
                        name=profile.display_name or extracted.vendor_name)
    if vendor is None:
        issues.append(Issue(severity=Severity.ERROR, code="vendor_unknown",
                            message=f"No GP vendor matches '{extracted.vendor_name}' "
                                    f"or domain '{sender_domain}'"))
    if not extracted.po_number:
        issues.append(Issue(severity=Severity.ERROR, code="po_missing",
                            message="No PO number found in the document"))
        return matched, issues, vendor

    po_lines = master.open_po_lines(extracted.po_number)
    if not po_lines:
        issues.append(Issue(severity=Severity.ERROR, code="po_not_open",
                            message=f"PO {extracted.po_number} not found among open POs"))
        return matched, issues, vendor
    if vendor and po_lines[0].vendor_id != vendor.vendor_id:
        issues.append(Issue(severity=Severity.ERROR, code="po_vendor_mismatch",
                            message=f"PO {extracted.po_number} belongs to "
                                    f"{po_lines[0].vendor_id}, not {vendor.vendor_id}"))

    by_gp_item = {ln.gp_item.lower(): ln for ln in po_lines}
    by_vendor_item = {ln.vendor_item.lower(): ln for ln in po_lines if ln.vendor_item}

    for line in extracted.lines:
        # resolution order: explicit GP item -> learned alias -> the PO's own
        # vendor-item cross-reference
        alias = (profile.item_map.get(line.vendor_item, "") if profile else "")
        po_line = (by_gp_item.get(line.gp_item.lower())
                   or by_gp_item.get(alias.lower())
                   or by_vendor_item.get(line.vendor_item.lower()))
        if po_line is None:
            issues.append(Issue(severity=Severity.ERROR, code="item_not_on_po",
                                line_no=line.line_no,
                                message=f"Line {line.line_no}: '{line.vendor_item or line.gp_item}' "
                                        f"({line.description}) is not on PO {extracted.po_number}"))
            continue

        if line.quantity <= 0:
            issues.append(Issue(severity=Severity.ERROR, code="qty_invalid",
                                line_no=line.line_no,
                                message=f"Line {line.line_no}: quantity {line.quantity} is not positive"))
            continue
        if line.quantity > po_line.qty_remaining:
            issues.append(Issue(
                severity=Severity.ERROR, code="qty_over_remaining", line_no=line.line_no,
                message=f"Line {line.line_no}: received {line.quantity} exceeds "
                        f"{po_line.qty_remaining} still open on PO {extracted.po_number}"))

        if line.unit_cost and po_line.unit_cost:
            drift = abs(line.unit_cost - po_line.unit_cost) / po_line.unit_cost
            if drift > PRICE_ERROR_TOLERANCE:
                issues.append(Issue(
                    severity=Severity.ERROR, code="price_variance", line_no=line.line_no,
                    message=f"Line {line.line_no}: unit cost {line.unit_cost} is "
                            f"{drift:.0%} off the PO price {po_line.unit_cost}"))
            elif drift > PRICE_WARN_TOLERANCE:
                issues.append(Issue(
                    severity=Severity.WARNING, code="price_variance", line_no=line.line_no,
                    message=f"Line {line.line_no}: unit cost {line.unit_cost} vs "
                            f"PO price {po_line.unit_cost} ({drift:.1%} variance)"))

        uom = line.uom
        if profile and uom in profile.uom_map:
            uom = profile.uom_map[uom]
        if po_line.uom and uom and uom.lower() != po_line.uom.lower():
            issues.append(Issue(
                severity=Severity.WARNING, code="uom_mismatch", line_no=line.line_no,
                message=f"Line {line.line_no}: document says '{uom}', PO uses '{po_line.uom}'"))

        matched.append(MatchedLine(
            line_no=line.line_no, gp_item=po_line.gp_item,
            vendor_item=line.vendor_item or po_line.vendor_item,
            description=line.description, quantity=line.quantity,
            uom=po_line.uom or uom, unit_cost=line.unit_cost or po_line.unit_cost,
            po_unit_cost=po_line.unit_cost, qty_remaining=po_line.qty_remaining))

    if not matched and not any(i.code == "po_not_open" for i in issues):
        issues.append(Issue(severity=Severity.ERROR, code="no_lines_matched",
                            message="No document lines could be matched to the PO"))
    return matched, issues, vendor
