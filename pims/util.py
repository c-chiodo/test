"""Small shared helpers: time, numbers, CSV."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def today_iso() -> str:
    return utc_now().date().isoformat()


def parse_dt(value: str | datetime | None) -> datetime | None:
    """Parse an ISO-8601 string tolerantly; always returns tz-aware UTC."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.combine(date.fromisoformat(text[:10]), datetime.min.time())
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hours_since(value: str | datetime | None) -> float | None:
    dt = parse_dt(value)
    if dt is None:
        return None
    return (utc_now() - dt).total_seconds() / 3600.0


def days_ago_iso(days: int) -> str:
    return (utc_now() - timedelta(days=days)).replace(microsecond=0).isoformat()


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def round_lbs(value: float | None) -> float:
    return round(float(value or 0.0), 2)


def rows_to_csv(rows: Iterable[dict[str, Any]], columns: list[str] | None = None) -> str:
    rows = list(rows)
    if columns is None:
        columns = list(rows[0].keys()) if rows else []
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c) for c in columns})
    return buf.getvalue()
