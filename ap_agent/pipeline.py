"""Orchestration: email in -> eConnect XML out (or review queue).

Flow per email:

1. dedup against the processed ledger (message-id)
2. look up / create the vendor profile from the sender domain
3. extract with Claude, feeding the profile's learned hints
4. validate against GP master data (open PO, quantities, prices)
5. route:
   - clean + trusted vendor + confident  -> auto-post (XML to outbox)
   - anything else                        -> review queue
6. learn: clean auto-posts bank trust; human corrections at approval time
   update the vendor's item aliases, UofM map and layout notes.

Auto-post is earned, never default: a vendor must have at least
``min_trusted_docs`` documents with a clean track record before the agent
posts without a human look.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .extraction import Extractor
from .gp import build_receipt_xml, write_outbox
from .matching import MasterData, validate
from .models import (Disposition, EmailDoc, ExtractedPO, Issue, ProcessResult,
                     Severity)
from .store import (Store, learn_from_correction, record_clean_extraction)


@dataclass
class Config:
    db_path: str = "data/ap_agent.db"
    outbox_dir: str = "data/ap_outbox"
    batch: str = "APAGENT"
    min_confidence: float = 0.85   # header + every line must clear this
    min_trusted_docs: int = 3      # clean docs before a vendor earns auto-post
    min_trust_score: float = 0.9
    auto_post_enabled: bool = True
    extra: dict = field(default_factory=dict)


class Pipeline:
    def __init__(self, config: Config, extractor: Extractor, master: MasterData,
                 store: Store | None = None):
        self.config = config
        self.extractor = extractor
        self.master = master
        self.store = store or Store(config.db_path)

    # -- main entry -----------------------------------------------------------

    def process_email(self, email_doc: EmailDoc) -> ProcessResult:
        if self.store.already_processed(email_doc.message_id):
            return ProcessResult(message_id=email_doc.message_id,
                                 disposition=Disposition.NOT_A_PO,
                                 issues=[Issue(severity=Severity.INFO, code="duplicate",
                                               message="Message already processed; skipped")])

        vendor_key = email_doc.sender_domain or email_doc.sender
        profile = self.store.get_or_create_profile(vendor_key)
        profile.extractions += 1

        extracted = self.extractor.extract(email_doc, profile)
        if not extracted.is_po_document:
            self.store.save_profile(profile)
            self.store.mark_processed(email_doc.message_id, Disposition.NOT_A_PO.value)
            return ProcessResult(message_id=email_doc.message_id, vendor_key=vendor_key,
                                 disposition=Disposition.NOT_A_PO, extracted=extracted)

        matched, issues, vendor = validate(
            extracted, profile, self.master, sender_domain=email_doc.sender_domain)
        if vendor:
            # remember the GP identity so future emails resolve instantly
            profile.gp_vendor_id = vendor.vendor_id
            profile.display_name = vendor.name
            if email_doc.sender_domain and email_doc.sender_domain not in profile.domains:
                profile.domains.append(email_doc.sender_domain)

        result = ProcessResult(
            message_id=email_doc.message_id, vendor_key=vendor_key,
            gp_vendor_id=vendor.vendor_id if vendor else "",
            extracted=extracted, matched_lines=matched, issues=issues,
            disposition=Disposition.NEEDS_REVIEW)

        if self._can_auto_post(profile, extracted, issues):
            record_clean_extraction(profile, extracted)
            self._post(result)
            result.disposition = Disposition.AUTO_POST
        else:
            result.review_id = self.store.enqueue_review(
                email_doc.message_id, vendor_key,
                {"result": result.model_dump(mode="json"),
                 "why": self._review_reasons(profile, extracted, issues)})

        self.store.save_profile(profile)
        self.store.mark_processed(email_doc.message_id, result.disposition.value)
        return result

    # -- review workflow ------------------------------------------------------

    def approve_review(self, review_id: int,
                       corrected: ExtractedPO | None = None) -> ProcessResult:
        """A human approved a queued item, optionally with corrections.

        Corrections are the agent's teacher: they update the vendor profile
        before the receipt is posted.
        """
        item = self.store.get_review(review_id)
        if item is None or item["status"] != "pending":
            raise ValueError(f"review {review_id} not found or not pending")
        result = ProcessResult.model_validate(item["payload"]["result"])
        profile = self.store.get_or_create_profile(item["vendor_key"])

        if corrected is not None:
            learn_from_correction(profile, result.extracted, corrected)
            result.extracted = corrected
            # re-validate with the corrected data and updated profile
            matched, issues, vendor = validate(
                corrected, profile, self.master,
                sender_domain=profile.domains[0] if profile.domains else "")
            result.matched_lines, result.issues = matched, issues
            if vendor:
                result.gp_vendor_id = vendor.vendor_id
        else:
            record_clean_extraction(profile, result.extracted)

        errors = [i for i in result.issues if i.severity == Severity.ERROR]
        if errors:
            raise ValueError(
                "cannot post: unresolved errors remain: "
                + "; ".join(i.message for i in errors))

        self._post(result)
        result.disposition = Disposition.AUTO_POST
        self.store.resolve_review(review_id, "approved")
        self.store.save_profile(profile)
        return result

    def reject_review(self, review_id: int) -> None:
        self.store.resolve_review(review_id, "rejected")

    # -- internals -------------------------------------------------------------

    def _can_auto_post(self, profile, extracted: ExtractedPO,
                       issues: list[Issue]) -> bool:
        if not self.config.auto_post_enabled:
            return False
        if any(i.severity == Severity.ERROR for i in issues):
            return False
        if extracted.confidence < self.config.min_confidence:
            return False
        if any(ln.confidence < self.config.min_confidence for ln in extracted.lines):
            return False
        # trust is earned: the doc counted in profile.extractions already,
        # so require the track record from *previous* documents
        prior_docs = profile.extractions - 1
        if prior_docs < self.config.min_trusted_docs or prior_docs <= 0:
            return False
        prior_trust = profile.clean_extractions / prior_docs
        return prior_trust >= self.config.min_trust_score

    def _review_reasons(self, profile, extracted: ExtractedPO,
                        issues: list[Issue]) -> list[str]:
        reasons = [i.message for i in issues if i.severity == Severity.ERROR]
        if extracted.confidence < self.config.min_confidence:
            reasons.append(f"header confidence {extracted.confidence:.2f} below "
                           f"{self.config.min_confidence}")
        low = [ln.line_no for ln in extracted.lines
               if ln.confidence < self.config.min_confidence]
        if low:
            reasons.append(f"low-confidence lines: {low}")
        prior_docs = profile.extractions - 1
        if prior_docs < self.config.min_trusted_docs:
            reasons.append(f"vendor still in training ({prior_docs}/"
                           f"{self.config.min_trusted_docs} documents reviewed)")
        return reasons or ["routine review"]

    def _post(self, result: ProcessResult) -> None:
        result.receipt_number = self.store.next_receipt_number()
        xml = build_receipt_xml(
            receipt_number=result.receipt_number,
            po_number=result.extracted.po_number,
            vendor_id=result.gp_vendor_id,
            lines=result.matched_lines,
            vendor_doc_number=result.extracted.vendor_doc_number,
            batch=self.config.batch)
        result.outbox_path = str(write_outbox(
            xml, result.receipt_number, self.config.outbox_dir))


def run_batch(pipeline: Pipeline, emails) -> list[ProcessResult]:
    return [pipeline.process_email(e) for e in emails]
