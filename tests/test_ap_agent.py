"""End-to-end tests for the AP agent using the offline stub extractor.

No network, no API key: extraction is the deterministic KeyValueStub, so
these tests exercise intake, matching, routing, eConnect output and — the
important part — the vendor-learning loop.
"""

from __future__ import annotations

import copy
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ap_agent.extraction import KeyValueStubExtractor, build_vendor_hints
from ap_agent.intake import EmlDirIntake
from ap_agent.matching import CsvMasterData, validate
from ap_agent.models import Disposition, EmailDoc, Severity
from ap_agent.pipeline import Config, Pipeline
from ap_agent.store import Store, VendorProfile, learn_from_correction

REPO = Path(__file__).resolve().parents[1]
EMAILS = REPO / "examples" / "ap_emails"
MASTER = REPO / "examples" / "gp_masterdata"


@pytest.fixture()
def pipeline(tmp_path):
    config = Config(db_path=str(tmp_path / "ap.db"),
                    outbox_dir=str(tmp_path / "outbox"))
    return Pipeline(config, KeyValueStubExtractor(), CsvMasterData(MASTER))


def _email(message_id: str, body: str,
           domain: str = "acmefeed.example.com") -> EmailDoc:
    return EmailDoc(message_id=message_id, sender=f"ship@{domain}",
                    sender_domain=domain, subject="shipment", body_text=body)


ACME_BODY = """\
po_number: {po}
vendor: Acme Feed Supplies
packing_slip: PS-{po}
date: 2026-08-14

lines:
ASM48 | Soybean Meal 48% | {qty} | TON | 412.50
"""


# -- intake -------------------------------------------------------------------

def test_eml_intake_parses_samples():
    docs = list(EmlDirIntake(EMAILS).fetch())
    assert len(docs) == 2
    acme = next(d for d in docs if "acmefeed" in d.sender_domain)
    assert acme.message_id == "<acme-ship-88121@acmefeed.example.com>"
    assert "PO-2001" in acme.body_text
    assert acme.sender_domain == "acmefeed.example.com"


# -- extraction stub -----------------------------------------------------------

def test_stub_extracts_header_and_lines():
    doc = _email("<m1>", ACME_BODY.format(po="PO-2001", qty=40))
    extracted = KeyValueStubExtractor().extract(doc, None)
    assert extracted.is_po_document
    assert extracted.po_number == "PO-2001"
    assert extracted.vendor_doc_number == "PS-PO-2001"
    assert len(extracted.lines) == 1
    assert extracted.lines[0].quantity == 40
    assert extracted.lines[0].uom == "TON"


def test_non_po_email_detected():
    doc = _email("<m2>", "Hi team, out of office until Monday.")
    extracted = KeyValueStubExtractor().extract(doc, None)
    assert not extracted.is_po_document


# -- matching ------------------------------------------------------------------

def test_validate_matches_by_po_vendor_item_crossref():
    master = CsvMasterData(MASTER)
    doc = _email("<m3>", ACME_BODY.format(po="PO-2001", qty=40))
    extracted = KeyValueStubExtractor().extract(doc, None)
    matched, issues, vendor = validate(extracted, None, master,
                                       sender_domain="acmefeed.example.com")
    assert vendor.vendor_id == "ACME001"
    assert [i for i in issues if i.severity == Severity.ERROR] == []
    assert matched[0].gp_item == "SOY-MEAL-48"


def test_validate_flags_over_receipt_and_price_variance():
    master = CsvMasterData(MASTER)
    body = ACME_BODY.format(po="PO-2001", qty=45).replace("412.50", "455.00")
    extracted = KeyValueStubExtractor().extract(_email("<m4>", body), None)
    _, issues, _ = validate(extracted, None, master,
                            sender_domain="acmefeed.example.com")
    codes = {i.code for i in issues}
    assert "qty_over_remaining" in codes
    assert "price_variance" in codes


def test_validate_unknown_po():
    master = CsvMasterData(MASTER)
    extracted = KeyValueStubExtractor().extract(
        _email("<m5>", ACME_BODY.format(po="PO-9999", qty=1)), None)
    _, issues, _ = validate(extracted, None, master,
                            sender_domain="acmefeed.example.com")
    assert any(i.code == "po_not_open" for i in issues)


# -- pipeline routing ------------------------------------------------------------

def test_new_vendor_goes_to_review_then_earns_auto_post(pipeline):
    # docs 1-3: clean extractions, but the vendor is still in training
    for i, po in enumerate(["PO-2001", "PO-2002", "PO-2003"], start=1):
        result = pipeline.process_email(
            _email(f"<t{i}>", ACME_BODY.format(po=po, qty=10)))
        assert result.disposition == Disposition.NEEDS_REVIEW
        pipeline.approve_review(result.review_id)  # human approves untouched

    # doc 4: trust earned (3 clean docs) -> straight through
    result = pipeline.process_email(
        _email("<t4>", ACME_BODY.format(po="PO-2004", qty=10)))
    assert result.disposition == Disposition.AUTO_POST
    assert result.receipt_number
    assert Path(result.outbox_path).exists()


def test_duplicate_message_skipped(pipeline):
    doc = _email("<dup>", ACME_BODY.format(po="PO-2001", qty=5))
    first = pipeline.process_email(doc)
    second = pipeline.process_email(doc)
    assert first.disposition == Disposition.NEEDS_REVIEW
    assert any(i.code == "duplicate" for i in second.issues)


def test_error_issues_block_auto_post_even_when_trusted(pipeline):
    for i, po in enumerate(["PO-2001", "PO-2002", "PO-2003"], start=1):
        r = pipeline.process_email(_email(f"<e{i}>", ACME_BODY.format(po=po, qty=5)))
        pipeline.approve_review(r.review_id)
    # over-receipt on a trusted vendor must still stop for review
    result = pipeline.process_email(
        _email("<e4>", ACME_BODY.format(po="PO-2004", qty=500)))
    assert result.disposition == Disposition.NEEDS_REVIEW
    with pytest.raises(ValueError):
        pipeline.approve_review(result.review_id)


# -- learning ---------------------------------------------------------------------

PRAIRIE_BODY = """\
po: PO-3001
vendor: Prairie Ingredients LLC
invoice: INV-4471

lines:
DDGS-BULK | DDGS | 60 | TON | 198.00
CHL-60 | Choline Chloride 60% | 5000 | LB | 0.92
"""


def test_correction_teaches_item_aliases_and_next_doc_matches(pipeline):
    domain = "prairieing.example.com"
    result = pipeline.process_email(_email("<p1>", PRAIRIE_BODY, domain))
    # Prairie's SKUs aren't on the PO's cross-reference -> errors -> review
    assert result.disposition == Disposition.NEEDS_REVIEW
    assert any(i.code == "item_not_on_po" for i in result.issues)

    # the AP clerk fills in the right GP items
    corrected = copy.deepcopy(result.extracted)
    corrected.lines[0].gp_item = "CORN-DDGS"
    corrected.lines[1].gp_item = "CHOLINE-60"
    posted = pipeline.approve_review(result.review_id, corrected)
    assert posted.receipt_number

    # the profile learned the aliases...
    profile = pipeline.store.get_profile(domain)
    assert profile.item_map == {"DDGS-BULK": "CORN-DDGS", "CHL-60": "CHOLINE-60"}

    # ...and the next document from Prairie resolves cleanly on its own
    body2 = PRAIRIE_BODY.replace("60 | TON", "30 | TON").replace("5000 | LB", "1000 | LB")
    result2 = pipeline.process_email(_email("<p2>", body2, domain))
    assert not [i for i in result2.issues if i.severity == Severity.ERROR]
    assert {ln.gp_item for ln in result2.matched_lines} == {"CORN-DDGS", "CHOLINE-60"}


def test_learn_from_correction_records_header_lessons():
    profile = VendorProfile(vendor_key="v")
    doc = _email("<h1>", ACME_BODY.format(po="PO-2001", qty=1))
    extracted = KeyValueStubExtractor().extract(doc, None)
    corrected = copy.deepcopy(extracted)
    corrected.po_number = "PO-2002"
    lessons = learn_from_correction(profile, extracted, corrected)
    assert any("po_number" in lesson for lesson in lessons)
    assert profile.corrected_fields >= 1
    hints = build_vendor_hints(profile)
    assert "PO-2002" in hints  # the lesson is replayed into the next prompt


def test_stub_applies_learned_aliases_like_prompt_hints_would():
    profile = VendorProfile(vendor_key="v", item_map={"ASM48": "SOY-MEAL-48"},
                            uom_map={"TON": "Ton"})
    doc = _email("<a1>", ACME_BODY.format(po="PO-2001", qty=2))
    extracted = KeyValueStubExtractor().extract(doc, profile)
    assert extracted.lines[0].gp_item == "SOY-MEAL-48"
    assert extracted.lines[0].uom == "Ton"


# -- eConnect output -----------------------------------------------------------------

def test_econnect_xml_shape(pipeline):
    for i, po in enumerate(["PO-2001", "PO-2002", "PO-2003"], start=1):
        r = pipeline.process_email(_email(f"<x{i}>", ACME_BODY.format(po=po, qty=5)))
        pipeline.approve_review(r.review_id)
    result = pipeline.process_email(
        _email("<x4>", ACME_BODY.format(po="PO-2004", qty=5)))
    assert result.disposition == Disposition.AUTO_POST

    root = ET.parse(result.outbox_path).getroot()
    assert root.tag == "eConnect"
    hdr = root.find("./POPReceivingsType/taPopRcptHdrInsert")
    assert hdr.findtext("POPRCTNM") == result.receipt_number
    assert hdr.findtext("POPTYPE") == "1"
    assert hdr.findtext("VENDORID") == "ACME001"
    assert hdr.findtext("BACHNUMB") == "APAGENT"
    lines = root.findall("./POPReceivingsType/taPopRcptLineInsert_Items/taPopRcptLineInsert")
    assert len(lines) == 1
    assert lines[0].findtext("PONUMBER") == "PO-2004"
    assert lines[0].findtext("ITEMNMBR") == "SOY-MEAL-48"
    assert lines[0].findtext("QTYSHPPD") == "5"
    assert lines[0].findtext("UNITCOST") == "412.50000"


def test_store_receipt_sequence(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.next_receipt_number() == "RCTAP000001"
    assert store.next_receipt_number() == "RCTAP000002"
