"""Command line: ``python -m pims <command>``.

The commands support needs at 2am, without a Python REPL:

    python -m pims init-db          create the schema (and seed if empty)
    python -m pims reset --yes      drop and rebuild the demo database
    python -m pims serve            run the app
    python -m pims diagnose         print the health checks (exit 1 if failed)
    python -m pims check            print data-quality findings
    python -m pims audit --limit 20 tail the audit trail
"""

from __future__ import annotations

import argparse
import json
import sys

from . import audit as audit_module
from . import db, health
from .config import get_settings


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pims", description="PIMS operations CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create the schema, seeding an empty database")

    reset = sub.add_parser("reset", help="delete and rebuild the local database")
    reset.add_argument("--yes", action="store_true", help="confirm the deletion")

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--reload", action="store_true")

    sub.add_parser("diagnose", help="run every health check")

    check = sub.add_parser("check", help="report data-quality findings")
    check.add_argument("--plant-id", type=int, default=None)

    audit_cmd = sub.add_parser("audit", help="tail the audit trail")
    audit_cmd.add_argument("--limit", type=int, default=20)
    audit_cmd.add_argument("--entity", default=None)
    audit_cmd.add_argument("--entity-id", default=None)

    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "init-db":
        db.init_db(settings)
        _print({"database": str(settings.sqlite_path), "tables": len(db.table_names())})
        return 0

    if args.command == "reset":
        if not args.yes:
            print("Refusing to delete without --yes.", file=sys.stderr)
            return 2
        db.close_connection()
        path = settings.sqlite_path
        for suffix in ("", "-wal", "-shm"):
            candidate = path.with_name(path.name + suffix)
            if candidate.exists():
                candidate.unlink()
        db.init_db(settings, seed=True)
        _print({"reset": str(path), "tables": len(db.table_names())})
        return 0

    if args.command == "serve":
        import uvicorn

        db.init_db(settings)
        uvicorn.run(
            "pims.app:app", host=args.host, port=args.port, reload=args.reload
        )
        return 0

    if args.command == "diagnose":
        db.init_db(settings)
        report = health.diagnostics()
        _print(report)
        return 1 if report["status"] == "failed" else 0

    if args.command == "check":
        db.init_db(settings)
        report = health.data_quality(args.plant_id)
        _print(report)
        return 0

    if args.command == "audit":
        db.init_db(settings)
        if args.entity and args.entity_id:
            _print(audit_module.for_entity(args.entity, args.entity_id, args.limit))
        else:
            _print(audit_module.recent(args.limit))
        return 0

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
