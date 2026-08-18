"""Email intake.

Two production sources plus a test-friendly one, all yielding ``EmailDoc``:

* ``EmlDirIntake``  — a drop folder of ``.eml`` files. Useful for testing and
  for shops that already export mail to disk (e.g. an Exchange transport
  rule or a Power Automate flow writing to a share).
* ``ImapIntake``    — polls an IMAP mailbox (works for Gmail, Microsoft 365
  and on-prem Exchange with IMAP enabled). Fetches UNSEEN messages and
  marks them seen once yielded.

Whatever the source, dedup against already-processed message-ids happens in
the pipeline, not here.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path

from .models import Attachment, EmailDoc

# Attachment types worth sending to the extractor. Everything else (logos,
# signatures, calendar invites) is noise.
_DOCUMENT_TYPES = {
    "application/pdf",
    "text/csv",
    "text/plain",
}


def _sender_domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].strip(">").lower() if "@" in addr else ""


def parse_message(msg: EmailMessage, fallback_id: str = "") -> EmailDoc:
    """Convert a stdlib EmailMessage into our normalized EmailDoc."""
    sender = email.utils.parseaddr(msg.get("From", ""))[1]
    body_text = ""
    attachments: list[Attachment] = []

    body_part = msg.get_body(preferencelist=("plain", "html"))
    if body_part is not None:
        body_text = body_part.get_content()

    for part in msg.iter_attachments():
        ctype = part.get_content_type()
        filename = part.get_filename() or "attachment"
        if ctype in _DOCUMENT_TYPES or filename.lower().endswith((".pdf", ".csv", ".txt")):
            payload = part.get_payload(decode=True) or b""
            if payload:
                attachments.append(Attachment(filename=filename, media_type=ctype, data=payload))

    return EmailDoc(
        message_id=(msg.get("Message-ID") or fallback_id or "").strip(),
        sender=sender,
        sender_domain=_sender_domain(sender),
        subject=msg.get("Subject", ""),
        body_text=body_text,
        attachments=attachments,
    )


class EmlDirIntake:
    """Reads every ``*.eml`` file in a directory."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def fetch(self) -> Iterator[EmailDoc]:
        for path in sorted(self.directory.glob("*.eml")):
            msg = email.message_from_bytes(
                path.read_bytes(), policy=email.policy.default)
            yield parse_message(msg, fallback_id=f"<file:{path.name}>")


class ImapIntake:
    """Polls an IMAP mailbox for unseen messages."""

    def __init__(self, host: str, username: str, password: str,
                 folder: str = "INBOX", port: int = 993):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.folder = folder

    def fetch(self) -> Iterator[EmailDoc]:
        conn = imaplib.IMAP4_SSL(self.host, self.port)
        try:
            conn.login(self.username, self.password)
            conn.select(self.folder)
            _, data = conn.search(None, "UNSEEN")
            for num in data[0].split():
                _, msg_data = conn.fetch(num, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                yield parse_message(msg, fallback_id=f"<imap:{num.decode()}>")
                conn.store(num, "+FLAGS", "\\Seen")
        finally:
            try:
                conn.logout()
            except Exception:
                pass
