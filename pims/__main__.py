"""Command line: ``python -m pims <command>``.

The commands support needs at 2am, without a Python REPL:

    python -m pims init-db          create the schema (and seed if empty)
    python -m pims reset --yes      drop and rebuild the demo database
    python -m pims serve            run the app
    python -m pims diagnose         print the health checks (exit 1 if failed)
    python -m pims check            print data-quality findings
    python -m pims audit --limit 20 tail the audit trail

Scheduled work (see docs/PIMS_RUNBOOK.md §13 for the crontab):

    python -m pims jobs daily       standing orders, auto-close, alerts, digest
    python -m pims jobs frequent    alert rules only — every few minutes
    python -m pims lims-sync        pull LIMS results into the projection
    python -m pims gp-sync          pull Great Plains master data
    python -m pims alerts           evaluate the rules (dry run unless --send)

Every command takes --dry-run where it would change something, and every run is
recorded in job_run so "did it run?" is answerable from the support console.
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

    jobs_cmd = sub.add_parser("jobs", help="run scheduled work")
    jobs_cmd.add_argument("which", choices=["daily", "frequent", "auto-close", "recurring", "status"])
    jobs_cmd.add_argument("--plant-id", type=int, default=None)
    jobs_cmd.add_argument("--dry-run", action="store_true")

    alerts_cmd = sub.add_parser("alerts", help="evaluate alert rules")
    alerts_cmd.add_argument("--plant-id", type=int, default=None)
    alerts_cmd.add_argument("--send", action="store_true", help="record and deliver (default is a dry run)")
    alerts_cmd.add_argument("--digest", action="store_true", help="send the daily digest instead")

    lims_cmd = sub.add_parser("lims-sync", help="pull LIMS results into the projection")
    lims_cmd.add_argument("--since-days", type=int, default=2)
    lims_cmd.add_argument("--dry-run", action="store_true")
    lims_cmd.add_argument("--mode", default=None, help="local | sqlserver (default: PIMS_LIMS_MODE)")
    lims_cmd.add_argument("--dsn", default=None, help="ODBC DSN for --mode sqlserver")

    gp_cmd = sub.add_parser("gp-sync", help="pull Great Plains master data")
    gp_cmd.add_argument("--file", default=None, help="JSON export to read instead of the stub")
    gp_cmd.add_argument("--dry-run", action="store_true")

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

    if args.command == "jobs":
        from .services import jobs as jobs_service

        db.init_db(settings)
        if args.which == "status":
            _print({"jobs": jobs_service.health_summary(), "recent": jobs_service.last_runs(10)})
        elif args.which == "daily":
            _print(jobs_service.daily(args.plant_id, send=not args.dry_run))
        elif args.which == "frequent":
            _print(jobs_service.frequent(args.plant_id, send=not args.dry_run))
        elif args.which == "auto-close":
            _print(jobs_service.auto_close(args.plant_id, dry_run=args.dry_run))
        else:
            _print(jobs_service.run_recurring(dry_run=args.dry_run))
        return 0

    if args.command == "alerts":
        from .services import alerts as alerts_service

        db.init_db(settings)
        if args.digest:
            _print(alerts_service.send_digest(args.plant_id) if args.send
                   else alerts_service.digest(args.plant_id))
        else:
            _print(alerts_service.run(args.plant_id, send=args.send))
        return 0

    if args.command == "lims-sync":
        from .integrations import lims_ingest

        db.init_db(settings)
        source = lims_ingest.build_source(args.mode, args.dsn)
        result = lims_ingest.sync(source, since_days=args.since_days, dry_run=args.dry_run)
        _print(result)
        return 0 if result.get("freshness", {}).get("status") != "failed" else 1

    if args.command == "gp-sync":
        from .integrations import gp_sync

        db.init_db(settings)
        source = gp_sync.FileSource(args.file) if args.file else gp_sync.StubSource()
        _print(gp_sync.sync(source, dry_run=args.dry_run))
        return 0

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
