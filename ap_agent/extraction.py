"""PO extraction from email content.

``ClaudeExtractor`` sends the email body plus any PDF/CSV/text attachments
to Claude with structured outputs, so the reply is a validated
``ExtractedPO`` — no JSON parsing, no regex. The vendor's learned profile
(layout notes, item aliases, UofM mappings) is injected as hints, which is
how the agent gets measurably better at a vendor after a few documents.

``KeyValueStubExtractor`` is a deterministic offline extractor used by the
test-suite and for dry runs without an API key.

Prompt-cache layout: the system prompt is stable and cached; per-vendor
hints and the email itself come after it in the user turn.
"""

from __future__ import annotations

import base64
import re
from typing import Protocol

from .models import EmailDoc, ExtractedLine, ExtractedPO
from .store import VendorProfile

SYSTEM_PROMPT = """\
You are an accounts-payable document extraction agent for a feed-ingredient
company that runs Microsoft Dynamics GP. You read a vendor email (body and
attachments) and extract purchase-order receipt data exactly as printed.

Rules:
- Copy values verbatim. Never invent an item code, quantity or price that
  is not in the document. Leave a field empty ("" or 0) rather than guess.
- "po_number" is OUR purchase-order number that the vendor references
  (often labelled PO, PO #, Purchase Order, Cust PO, Your Order).
  "vendor_doc_number" is THEIR document number (Invoice #, Packing Slip #,
  Order #, BOL #).
- One ExtractedLine per shipped/ordered line item. line_no counts from 1
  in document order.
- Set is_po_document=false for anything that is not an order document
  (marketing, statements without line detail, out-of-office replies).
- Use the per-line and overall confidence fields honestly; low confidence
  routes the document to a human instead of straight into the ERP.
- In layout_observations, note where the key fields live in THIS vendor's
  layout so the note can be replayed to speed up the next document from
  them. If vendor hints are provided below, follow them — they encode past
  human corrections for this exact vendor."""


class Extractor(Protocol):
    def extract(self, email_doc: EmailDoc, profile: VendorProfile | None) -> ExtractedPO: ...


def build_vendor_hints(profile: VendorProfile | None) -> str:
    """Render the vendor's learned profile as prompt hints."""
    if profile is None:
        return "No prior history with this vendor. Extract carefully and record layout observations."
    parts: list[str] = [f"Vendor: {profile.display_name or profile.vendor_key}"]
    if profile.gp_vendor_id:
        parts.append(f"GP vendor id: {profile.gp_vendor_id}")
    if profile.layout_notes:
        parts.append("Layout lessons from previous documents (follow these):")
        parts.extend(f"  - {note}" for note in profile.layout_notes)
    if profile.item_map:
        parts.append("Known item aliases (vendor SKU -> our GP item number). "
                     "When a line shows one of these vendor SKUs, fill gp_item accordingly:")
        parts.extend(f"  - {v} -> {g}" for v, g in sorted(profile.item_map.items()))
    if profile.uom_map:
        parts.append("Unit-of-measure conventions for this vendor (theirs -> ours):")
        parts.extend(f"  - {v} -> {g}" for v, g in sorted(profile.uom_map.items()))
    parts.append(f"Track record: {profile.extractions} documents seen, "
                 f"{profile.trust_score():.0%} needed no human correction.")
    return "\n".join(parts)


class ClaudeExtractor:
    """Extraction via the Claude API with structured outputs."""

    def __init__(self, model: str = "claude-opus-5", max_tokens: int = 16000):
        import anthropic  # optional dependency: pip install .[ap]
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def extract(self, email_doc: EmailDoc, profile: VendorProfile | None) -> ExtractedPO:
        content: list[dict] = [
            {"type": "text",
             "text": ("VENDOR HINTS\n" + build_vendor_hints(profile) +
                      "\n\nEMAIL\n"
                      f"From: {email_doc.sender}\n"
                      f"Subject: {email_doc.subject}\n\n"
                      f"{email_doc.body_text}")},
        ]
        for att in email_doc.attachments:
            if att.media_type == "application/pdf":
                content.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(att.data).decode("ascii"),
                    },
                })
            else:  # csv / plain text ride along inline
                content.append({
                    "type": "text",
                    "text": f"ATTACHMENT {att.filename}\n{att.data.decode('utf-8', 'replace')}",
                })

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_format=ExtractedPO,
        )
        if response.stop_reason == "refusal":
            return ExtractedPO(is_po_document=False, confidence=0.0,
                               layout_observations="model declined to process this document")
        return response.parsed_output


class KeyValueStubExtractor:
    """Offline extractor for tests/dry runs.

    Parses a rigid ``field: value`` format plus pipe-delimited line tables:

        po_number: PO-2001
        vendor: Acme Feed Supplies
        lines:
        vendor_item | description | qty | uom | unit_cost

    It also applies profile item/UofM maps the same way the prompt hints
    would, so learning behaviour is testable without the API.
    """

    HEADER_FIELDS = {
        "po_number": "po_number",
        "po": "po_number",
        "vendor": "vendor_name",
        "invoice": "vendor_doc_number",
        "packing_slip": "vendor_doc_number",
        "date": "doc_date",
        "total": "document_total",
    }

    def extract(self, email_doc: EmailDoc, profile: VendorProfile | None) -> ExtractedPO:
        text = email_doc.body_text
        for att in email_doc.attachments:
            if att.media_type.startswith("text/"):
                text += "\n" + att.data.decode("utf-8", "replace")

        result = ExtractedPO(is_po_document=False, confidence=0.9)
        in_lines = False
        line_no = 0
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.lower() == "lines:":
                in_lines = True
                continue
            if in_lines and "|" in line:
                cells = [c.strip() for c in line.split("|")]
                if len(cells) < 5 or cells[2].lower() in ("qty", "quantity"):
                    continue
                line_no += 1
                vendor_item, desc, qty, uom, cost = cells[:5]
                gp_item = (profile.item_map.get(vendor_item, "") if profile else "")
                uom = (profile.uom_map.get(uom, uom) if profile else uom)
                result.lines.append(ExtractedLine(
                    line_no=line_no, vendor_item=vendor_item, gp_item=gp_item,
                    description=desc, quantity=float(qty), uom=uom,
                    unit_cost=float(cost), confidence=0.9))
                continue
            m = re.match(r"^([A-Za-z_ ]+):\s*(.+)$", line)
            if m:
                key = m.group(1).strip().lower().replace(" ", "_")
                field = self.HEADER_FIELDS.get(key)
                if field == "document_total":
                    result.document_total = float(m.group(2).replace(",", "").replace("$", ""))
                elif field:
                    setattr(result, field, m.group(2).strip())

        result.is_po_document = bool(result.po_number and result.lines)
        return result
