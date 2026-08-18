"""Command line interface for the AP agent.

    # process a folder of .eml files against CSV master data (dry run w/o API)
    python -m ap_agent process --eml-dir examples/ap_emails \
        --masterdata examples/gp_masterdata --stub

    # same but live extraction with Claude (needs ANTHROPIC_API_KEY)
    python -m ap_agent process --eml-dir /mail/drop --masterdata /gp/snapshot

    # poll an IMAP mailbox
    python -m ap_agent process --imap-host imap.example.com \
        --imap-user ap@feedenergy.com --masterdata /gp/snapshot

    # review queue
    python -m ap_agent review list
    python -m ap_agent review show 3
    python -m ap_agent review approve 3 [--corrected corrected.json]
    python -m ap_agent review reject 3

    # what has the agent learned about each vendor?
    python -m ap_agent vendors
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .extraction import ClaudeExtractor, KeyValueStubExtractor
from .intake import EmlDirIntake, ImapIntake
from .matching import CsvMasterData
from .models import ExtractedPO
from .pipeline import Config, Pipeline
from .store import Store


def _build_pipeline(args) -> Pipeline:
    config = Config(db_path=args.db, outbox_dir=args.outbox)
    master = CsvMasterData(args.masterdata)
    extractor = KeyValueStubExtractor() if args.stub else ClaudeExtractor()
    return Pipeline(config, extractor, master)


def cmd_process(args) -> int:
    pipeline = _build_pipeline(args)
    if args.eml_dir:
        intake = EmlDirIntake(args.eml_dir)
    elif args.imap_host:
        password = os.environ.get("AP_IMAP_PASSWORD", "")
        if not password:
            print("set AP_IMAP_PASSWORD in the environment", file=sys.stderr)
            return 2
        intake = ImapIntake(args.imap_host, args.imap_user, password)
    else:
        print("pass --eml-dir or --imap-host", file=sys.stderr)
        return 2

    for email_doc in intake.fetch():
        result = pipeline.process_email(email_doc)
        tag = result.disposition.value.upper()
        line = f"[{tag:12s}] {email_doc.subject!r} from {email_doc.sender}"
        if result.receipt_number:
            line += f" -> {result.outbox_path}"
        if result.review_id:
            line += f" -> review #{result.review_id}"
        print(line)
        for issue in result.issues:
            print(f"    {issue.severity.value}: {issue.message}")
    return 0


def cmd_review(args) -> int:
    if args.action == "list":
        store = Store(args.db)
        items = store.list_reviews()
        if not items:
            print("review queue is empty")
        for item in items:
            result = item["payload"]["result"]
            ext = result.get("extracted") or {}
            print(f"#{item['id']:<4} {item['vendor_key']:<28} "
                  f"PO {ext.get('po_number', '?'):<12} "
                  f"{len(ext.get('lines', []))} lines  ({item['created_at']})")
            for why in item["payload"].get("why", []):
                print(f"      - {why}")
        return 0

    if args.action == "show":
        store = Store(args.db)
        item = store.get_review(args.review_id)
        if item is None:
            print(f"no review #{args.review_id}", file=sys.stderr)
            return 1
        print(json.dumps(item["payload"], indent=2))
        return 0

    pipeline = _build_pipeline(args)
    if args.action == "approve":
        corrected = None
        if args.corrected:
            corrected = ExtractedPO.model_validate_json(
                Path(args.corrected).read_text())
        result = pipeline.approve_review(args.review_id, corrected)
        print(f"posted {result.receipt_number} -> {result.outbox_path}")
        return 0
    if args.action == "reject":
        pipeline.reject_review(args.review_id)
        print(f"review #{args.review_id} rejected")
        return 0
    return 2


def cmd_vendors(args) -> int:
    store = Store(args.db)
    profiles = store.list_profiles()
    if not profiles:
        print("no vendors learned yet")
    for p in profiles:
        print(f"{p.vendor_key}  (GP: {p.gp_vendor_id or '?'})  "
              f"docs={p.extractions} clean={p.clean_extractions} "
              f"trust={p.trust_score():.0%}")
        for sku, item in sorted(p.item_map.items()):
            print(f"    alias {sku} -> {item}")
        for note in p.layout_notes:
            print(f"    note: {note}")
    return 0


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="data/ap_agent.db")
    parser.add_argument("--outbox", default="data/ap_outbox")
    parser.add_argument("--masterdata", default="examples/gp_masterdata",
                        help="directory with vendors.csv and open_po_lines.csv")
    parser.add_argument("--stub", action="store_true",
                        help="use the offline stub extractor (no API calls)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ap_agent", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("process", help="fetch and process PO emails")
    p.add_argument("--eml-dir", help="directory of .eml files")
    p.add_argument("--imap-host")
    p.add_argument("--imap-user")
    _common(p)
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("review", help="work the human review queue")
    p.add_argument("action", choices=["list", "show", "approve", "reject"])
    p.add_argument("review_id", nargs="?", type=int)
    p.add_argument("--corrected", help="JSON file with the corrected ExtractedPO")
    _common(p)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("vendors", help="show learned vendor profiles")
    _common(p)
    p.set_defaults(func=cmd_vendors)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
