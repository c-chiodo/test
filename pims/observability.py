"""Request logging, timing and a bounded buffer of recent failures.

Support work on the legacy system started with a screenshot of a message box.
This module makes the first question — "what actually happened, and how often?"
— answerable from inside the app: every request is timed, every failure is kept
with its correlation id, and the support console reads both.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from typing import Any

from .util import utc_now_iso

logger = logging.getLogger("pims")

_MAX_ERRORS = 200
_MAX_SLOW = 50

_lock = threading.Lock()
_errors: deque[dict[str, Any]] = deque(maxlen=_MAX_ERRORS)
_slow: deque[dict[str, Any]] = deque(maxlen=_MAX_SLOW)
_counts: Counter[str] = Counter()
_started_at = utc_now_iso()
_slow_threshold_ms = 750.0


def configure_logging(level: int = logging.INFO) -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(level)


def record_request(
    *,
    method: str,
    path: str,
    status: int,
    duration_ms: float,
    correlation_id: str,
    username: str | None = None,
) -> None:
    with _lock:
        _counts["requests"] += 1
        if status >= 500:
            _counts["server_errors"] += 1
        elif status >= 400:
            _counts["client_errors"] += 1
        if duration_ms >= _slow_threshold_ms:
            _counts["slow"] += 1
            _slow.append(
                {
                    "at": utc_now_iso(),
                    "method": method,
                    "path": path,
                    "duration_ms": round(duration_ms, 1),
                    "correlation_id": correlation_id,
                }
            )
    logger.info(
        "%s %s -> %s in %.1fms cid=%s user=%s",
        method,
        path,
        status,
        duration_ms,
        correlation_id,
        username or "-",
    )


def record_error(
    *,
    method: str,
    path: str,
    code: str,
    message: str,
    correlation_id: str,
    username: str | None = None,
    traceback_text: str | None = None,
) -> None:
    entry = {
        "at": utc_now_iso(),
        "method": method,
        "path": path,
        "code": code,
        "message": message,
        "correlation_id": correlation_id,
        "username": username,
        "traceback": traceback_text,
    }
    with _lock:
        _errors.append(entry)
        _counts[f"error:{code}"] += 1
    logger.warning("%s %s failed: %s (%s) cid=%s", method, path, message, code, correlation_id)


def recent_errors(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        return list(_errors)[-limit:][::-1]


def slow_requests(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        return list(_slow)[-limit:][::-1]


def counters() -> dict[str, int]:
    with _lock:
        return dict(_counts)


def uptime() -> dict[str, Any]:
    return {"started_at": _started_at, "uptime_seconds": round(time.monotonic(), 1)}


def reset() -> None:
    """Test helper."""

    with _lock:
        _errors.clear()
        _slow.clear()
        _counts.clear()
