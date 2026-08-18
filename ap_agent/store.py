"""Persistence: vendor memory, review queue, processed ledger, receipt numbers.

Everything lives in one SQLite file so the agent can run anywhere with zero
infrastructure. The interesting part is the vendor memory — see
``VendorProfile`` and ``learn_from_correction``: this is what makes the
agent get smarter about each vendor's layout over time.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Optional

from pydantic import BaseModel, Field

from .models import ExtractedPO

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vendor_profiles (
    vendor_key TEXT PRIMARY KEY,
    profile    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT NOT NULL,
    vendor_key  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS processed (
    message_id   TEXT PRIMARY KEY,
    disposition  TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sequences (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class VendorProfile(BaseModel):
    """Everything the agent has learned about one vendor.

    ``layout_notes`` are replayed into the extraction prompt on the next
    email from this vendor, so a lesson learned once ("PO number is in the
    subject, not the body") applies to every future document.
    """

    vendor_key: str                       # normally the sender domain
    gp_vendor_id: str = ""                # Dynamics GP VENDORID
    display_name: str = ""
    domains: list[str] = Field(default_factory=list)
    # vendor SKU -> our GP item number, built up from human corrections
    item_map: dict[str, str] = Field(default_factory=dict)
    # vendor's printed UofM -> GP UofM ("CTN" -> "Case")
    uom_map: dict[str, str] = Field(default_factory=dict)
    # free-text lessons about this vendor's document layout
    layout_notes: list[str] = Field(default_factory=list)
    # trust stats: extractions seen, how many sailed through untouched
    extractions: int = 0
    clean_extractions: int = 0
    corrected_fields: int = 0
    updated_at: str = ""

    MAX_NOTES: ClassVar[int] = 12

    def add_note(self, note: str) -> None:
        note = note.strip()
        if note and note not in self.layout_notes:
            self.layout_notes.append(note)
            # keep the newest lessons; the oldest are usually superseded
            self.layout_notes = self.layout_notes[-self.MAX_NOTES:]

    def trust_score(self) -> float:
        """0..1 — how often this vendor's extractions needed no human edits."""
        if self.extractions == 0:
            return 0.0
        return self.clean_extractions / self.extractions


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- vendor memory ------------------------------------------------------

    def get_profile(self, vendor_key: str) -> Optional[VendorProfile]:
        row = self._conn.execute(
            "SELECT profile FROM vendor_profiles WHERE vendor_key = ?",
            (vendor_key,)).fetchone()
        return VendorProfile.model_validate_json(row["profile"]) if row else None

    def get_or_create_profile(self, vendor_key: str) -> VendorProfile:
        return self.get_profile(vendor_key) or VendorProfile(vendor_key=vendor_key)

    def save_profile(self, profile: VendorProfile) -> None:
        profile.updated_at = _now()
        self._conn.execute(
            "INSERT INTO vendor_profiles (vendor_key, profile) VALUES (?, ?) "
            "ON CONFLICT(vendor_key) DO UPDATE SET profile = excluded.profile",
            (profile.vendor_key, profile.model_dump_json()))
        self._conn.commit()

    def list_profiles(self) -> list[VendorProfile]:
        rows = self._conn.execute(
            "SELECT profile FROM vendor_profiles ORDER BY vendor_key").fetchall()
        return [VendorProfile.model_validate_json(r["profile"]) for r in rows]

    # -- processed ledger (idempotency) --------------------------------------

    def already_processed(self, message_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM processed WHERE message_id = ?", (message_id,)).fetchone()
        return row is not None

    def mark_processed(self, message_id: str, disposition: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO processed (message_id, disposition, processed_at) "
            "VALUES (?, ?, ?)", (message_id, disposition, _now()))
        self._conn.commit()

    # -- review queue --------------------------------------------------------

    def enqueue_review(self, message_id: str, vendor_key: str, payload: dict) -> int:
        cur = self._conn.execute(
            "INSERT INTO review_queue (message_id, vendor_key, payload, created_at) "
            "VALUES (?, ?, ?, ?)",
            (message_id, vendor_key, json.dumps(payload), _now()))
        self._conn.commit()
        return cur.lastrowid

    def get_review(self, review_id: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM review_queue WHERE id = ?", (review_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        return item

    def list_reviews(self, status: str = "pending") -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM review_queue WHERE status = ? ORDER BY id", (status,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            out.append(item)
        return out

    def resolve_review(self, review_id: int, status: str) -> None:
        self._conn.execute(
            "UPDATE review_queue SET status = ?, resolved_at = ? WHERE id = ?",
            (status, _now(), review_id))
        self._conn.commit()

    # -- receipt number sequence ----------------------------------------------

    def next_receipt_number(self, prefix: str = "RCTAP") -> str:
        row = self._conn.execute(
            "SELECT value FROM sequences WHERE name = 'receipt'").fetchone()
        value = (row["value"] if row else 0) + 1
        self._conn.execute(
            "INSERT INTO sequences (name, value) VALUES ('receipt', ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value", (value,))
        self._conn.commit()
        return f"{prefix}{value:06d}"


# --------------------------------------------------------------------------
# Learning
# --------------------------------------------------------------------------

def learn_from_correction(profile: VendorProfile,
                          extracted: ExtractedPO,
                          corrected: ExtractedPO) -> list[str]:
    """Fold a human correction back into the vendor's profile.

    Returns human-readable descriptions of what was learned. Deterministic
    on purpose: alias/UofM mappings must be exact, not model-paraphrased.
    """
    lessons: list[str] = []
    corrected_fields = 0

    # header fields — record layout lessons for anything the human changed
    for field in ("po_number", "vendor_doc_number", "doc_date", "vendor_name"):
        old, new = getattr(extracted, field), getattr(corrected, field)
        if old != new and new:
            corrected_fields += 1
            note = f"{field}: previously misread as '{old}', correct value looked like '{new}'"
            profile.add_note(note)
            lessons.append(note)

    # line-level: item aliases and UofM mappings, matched by line number
    ext_by_no = {ln.line_no: ln for ln in extracted.lines}
    for cor in corrected.lines:
        ext = ext_by_no.get(cor.line_no)
        if ext is None:
            continue
        if cor.gp_item and cor.vendor_item and profile.item_map.get(cor.vendor_item) != cor.gp_item:
            profile.item_map[cor.vendor_item] = cor.gp_item
            lessons.append(f"item alias: vendor '{cor.vendor_item}' -> GP '{cor.gp_item}'")
            if ext.gp_item != cor.gp_item:
                corrected_fields += 1
        if cor.uom and ext.uom and cor.uom != ext.uom:
            profile.uom_map[ext.uom] = cor.uom
            lessons.append(f"uom mapping: vendor '{ext.uom}' -> GP '{cor.uom}'")
            corrected_fields += 1
        for field in ("quantity", "unit_cost"):
            if getattr(ext, field) != getattr(cor, field):
                corrected_fields += 1
                profile.add_note(
                    f"line {cor.line_no} {field}: read {getattr(ext, field)}, "
                    f"human corrected to {getattr(cor, field)}")

    profile.corrected_fields += corrected_fields
    if corrected_fields == 0:
        profile.clean_extractions += 1
    return lessons


def record_clean_extraction(profile: VendorProfile,
                            extracted: ExtractedPO) -> None:
    """An extraction went through with no human edits — bank the trust and
    keep the model's own layout observations as future hints."""
    profile.clean_extractions += 1
    if extracted.layout_observations:
        profile.add_note(extracted.layout_observations)
