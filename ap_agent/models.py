"""Data model for the AP agent.

Everything the pipeline passes between stages is a pydantic model, so the
review queue can round-trip items through JSON and the extraction step can
use Claude structured outputs directly.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------
# Inbound email
# --------------------------------------------------------------------------

class Attachment(BaseModel):
    filename: str
    media_type: str
    data: bytes

    # bytes stay bytes in memory; the review queue never stores raw
    # attachments, only the extraction result.
    model_config = ConfigDict(arbitrary_types_allowed=True)


class EmailDoc(BaseModel):
    """A normalized inbound email, whatever the source (IMAP, .eml drop)."""

    message_id: str
    sender: str
    sender_domain: str
    subject: str
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    body_text: str = ""
    attachments: list[Attachment] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


# --------------------------------------------------------------------------
# Extraction result (Claude structured output — keep JSON-schema friendly)
# --------------------------------------------------------------------------

class ExtractedLine(BaseModel):
    """One order line as it appears in the vendor's document."""

    line_no: int = Field(description="1-based position of the line in the document")
    vendor_item: str = Field(default="", description="The vendor's own item/SKU code, verbatim")
    gp_item: str = Field(default="", description="Our internal GP item number if the document shows it, else empty")
    description: str = Field(default="", description="Line item description, verbatim")
    quantity: float = Field(description="Quantity shipped/ordered on this line")
    uom: str = Field(default="", description="Unit of measure as printed, e.g. EA, LB, TON, CS")
    unit_cost: float = Field(default=0.0, description="Unit price. 0 if not shown")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0,
                              description="Your confidence that every field on this line is correct")


class ExtractedPO(BaseModel):
    """The structured order data pulled out of one email."""

    is_po_document: bool = Field(
        description="True only if the email actually contains a purchase order, "
                    "order confirmation, packing slip or invoice with line items")
    vendor_name: str = Field(default="", description="Vendor/supplier company name as printed")
    po_number: str = Field(default="", description="OUR purchase order number the vendor references")
    vendor_doc_number: str = Field(default="", description="The vendor's own document number "
                                                           "(invoice #, packing slip #, order #)")
    doc_date: str = Field(default="", description="Document date, ISO format YYYY-MM-DD if determinable")
    currency: str = Field(default="USD")
    lines: list[ExtractedLine] = Field(default_factory=list)
    document_total: float = Field(default=0.0, description="Grand total if printed, else 0")
    layout_observations: str = Field(
        default="",
        description="1-3 short sentences on where each field was found in this vendor's layout "
                    "(e.g. 'PO number is in the subject line; prices are in the PDF table, col 5'). "
                    "These are saved and replayed to help parse this vendor next time.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0,
                              description="Overall confidence in the header fields (vendor, PO number, date)")


# --------------------------------------------------------------------------
# Validation & disposition
# --------------------------------------------------------------------------

class Severity(str, enum.Enum):
    ERROR = "error"       # blocks auto-post, forces review/rejection
    WARNING = "warning"   # allowed through, surfaced to the reviewer
    INFO = "info"


class Issue(BaseModel):
    severity: Severity
    code: str
    message: str
    line_no: Optional[int] = None


class MatchedLine(BaseModel):
    """An extracted line resolved against the open PO in GP."""

    line_no: int
    gp_item: str
    vendor_item: str = ""
    description: str = ""
    quantity: float
    uom: str
    unit_cost: float
    po_unit_cost: float = 0.0
    qty_remaining: float = 0.0


class Disposition(str, enum.Enum):
    AUTO_POST = "auto_post"        # written straight to the eConnect outbox
    NEEDS_REVIEW = "needs_review"  # parked in the review queue
    NOT_A_PO = "not_a_po"          # email had no order document in it
    REJECTED = "rejected"


class ProcessResult(BaseModel):
    message_id: str
    disposition: Disposition
    vendor_key: str = ""
    gp_vendor_id: str = ""
    extracted: Optional[ExtractedPO] = None
    matched_lines: list[MatchedLine] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    receipt_number: str = ""
    outbox_path: str = ""
    review_id: Optional[int] = None
