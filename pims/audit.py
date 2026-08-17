"""Audit trail.

One function, used by every write path. It exists because the first question
support asks about a production issue — "who changed this, when, and from what
to what?" — could not be answered in the legacy system.
"""

from __future__ import annotations

import json
from typing import Any

from . import db
from .util import utc_now_iso


def record(
    *,
    username: str,
    action: str,
    entity: str,
    entity_id: Any,
    order_id: int | None = None,
    summary: str = "",
    detail: dict[str, Any] | None = None,
    conn=None,
) -> int:
    return db.insert(
        "audit_log",
        {
            "occurred_at": utc_now_iso(),
            "username": username,
            "action": action,
            "entity": entity,
            "entity_id": str(entity_id),
            "order_id": int(order_id) if order_id else None,
            "summary": summary,
            "detail_json": json.dumps(detail or {}, default=str),
        },
        conn,
    )


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Field-level before/after, restricted to what actually changed."""

    changed: dict[str, Any] = {}
    for key, new in after.items():
        old = before.get(key)
        if old != new:
            changed[key] = {"from": old, "to": new}
    return changed


def for_entity(entity: str, entity_id: Any, limit: int = 100, conn=None) -> list[dict]:
    rows = db.query(
        """
        SELECT audit_id, occurred_at, username, action, entity, entity_id,
               summary, detail_json
        FROM audit_log
        WHERE entity = ? AND entity_id = ?
        ORDER BY audit_id DESC
        LIMIT ?
        """,
        (entity, str(entity_id), limit),
        conn,
    )
    return [_hydrate(r) for r in rows]


def for_order(order_id: int, limit: int = 200, conn=None) -> list[dict]:
    """Everything that happened to an order, not only edits to the order row.

    Loads, ships, voids and QC records are recorded against their own entity,
    which is correct — but a supervisor asking "what did the operator do to
    this order?" wants them in one list, in order, which is what this returns.
    """

    rows = db.query(
        """
        SELECT audit_id, occurred_at, username, action, entity, entity_id,
               summary, detail_json
        FROM audit_log
        WHERE order_id = ?
           OR (entity = 'order' AND entity_id = ?)
        ORDER BY audit_id DESC
        LIMIT ?
        """,
        (int(order_id), str(order_id), limit),
        conn,
    )
    return [_hydrate(r) for r in rows]


def recent(limit: int = 100, username: str | None = None, conn=None) -> list[dict]:
    sql = """
        SELECT audit_id, occurred_at, username, action, entity, entity_id,
               summary, detail_json
        FROM audit_log
    """
    params: list[Any] = []
    if username:
        sql += " WHERE username = ?"
        params.append(username)
    sql += " ORDER BY audit_id DESC LIMIT ?"
    params.append(limit)
    return [_hydrate(r) for r in db.query(sql, params, conn)]


def _hydrate(row: dict) -> dict:
    row = dict(row)
    raw = row.pop("detail_json", "{}")
    try:
        row["detail"] = json.loads(raw)
    except json.JSONDecodeError:
        row["detail"] = {"unparsed": raw}
    return row
