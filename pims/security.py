"""Authentication, roles and plant access.

The legacy client authenticated against the company Windows account and stored
per-user plant/company access rows. This keeps the same access model — a user
sees only the plants granted to them — behind a token session so the same rules
apply to a browser, a script, or an integration.

Passwords use PBKDF2-HMAC-SHA256 from the standard library. If the deployment
sits behind SSO (Entra ID / AD FS), :func:`user_from_token` is the single seam
to replace; nothing else in the app inspects credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import timedelta
from typing import Any

from . import audit, db
from .config import get_settings
from .errors import AuthError, PermissionError_, ValidationError
from .util import parse_dt, utc_now, utc_now_iso

_PBKDF2_ROUNDS = 240_000

ROLES = ("operator", "qc", "supervisor", "admin")

#: What each role may do. Checked by :func:`require_permission`.
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "operator": {"order.read", "txn.read", "txn.post", "qc.read", "query.run"},
    "qc": {
        "order.read",
        "txn.read",
        "qc.read",
        "qc.write",
        "query.run",
        "spec.read",
    },
    "supervisor": {
        "order.read",
        "order.write",
        "order.close",
        "txn.read",
        "txn.post",
        "txn.void",
        "qc.read",
        "qc.write",
        "query.run",
        "spec.read",
        "support.read",
    },
    "admin": {"*"},
}


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ROUNDS
    )
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(rounds)
    )
    return hmac.compare_digest(candidate.hex(), digest)


def get_user(username: str, conn=None) -> dict | None:
    return db.query_one(
        "SELECT * FROM app_user WHERE username = ? AND active = 1", (username,), conn
    )


def plants_for_user(user_id: int, conn=None) -> list[dict]:
    return db.query(
        """
        SELECT p.plant_id, p.code, p.name
        FROM user_plant_access upa
        JOIN plant p ON p.plant_id = upa.plant_id
        WHERE upa.user_id = ? AND p.active = 1
        ORDER BY p.code
        """,
        (user_id,),
        conn,
    )


def login(username: str, password: str, conn=None) -> dict[str, Any]:
    user = get_user(username, conn)
    if user is None or not verify_password(password, user["password_hash"]):
        raise AuthError("Username or password is incorrect.")
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    expires = utc_now() + timedelta(hours=settings.session_hours)
    db.insert(
        "user_session",
        {
            "token": token,
            "user_id": user["user_id"],
            "created_at": utc_now_iso(),
            "expires_at": expires.replace(microsecond=0).isoformat(),
        },
        conn,
    )
    audit.record(
        username=username,
        action="login",
        entity="user",
        entity_id=user["user_id"],
        summary=f"{username} signed in",
        conn=conn,
    )
    return {"token": token, "expires_at": expires.isoformat(), "user": public_user(user, conn)}


def login_with_pin(username: str, pin: str, plant_id: int | None = None, conn=None) -> dict[str, Any]:
    """Kiosk sign-in: a short PIN, a short session.

    A shared loadout terminal cannot ask for a password every load and cannot
    stay signed in as whoever used it last. The PIN identifies the operator for
    the audit trail; the session is deliberately brief (``kiosk_session_minutes``)
    so an unattended screen stops being someone else's account.
    """

    user = get_user(username, conn)
    if user is None or not user.get("pin_hash") or not verify_password(pin, user["pin_hash"]):
        raise AuthError("That PIN was not recognised.")
    if plant_id is not None:
        require_plant(user, plant_id, conn)

    minutes = int(os.environ.get("PIMS_KIOSK_SESSION_MINUTES", "30"))
    token = secrets.token_urlsafe(32)
    expires = utc_now() + timedelta(minutes=minutes)
    db.insert(
        "user_session",
        {
            "token": token,
            "user_id": user["user_id"],
            "created_at": utc_now_iso(),
            "expires_at": expires.replace(microsecond=0).isoformat(),
        },
        conn,
    )
    audit.record(
        username=username,
        action="login.pin",
        entity="user",
        entity_id=user["user_id"],
        summary=f"{username} signed in at a kiosk",
        detail={"plant_id": plant_id},
        conn=conn,
    )
    return {
        "token": token,
        "expires_at": expires.isoformat(),
        "session_minutes": minutes,
        "user": public_user(user, conn),
    }


def kiosk_users(plant_id: int, conn=None) -> list[dict]:
    """Who can sign in at this plant's terminal — the picker on the kiosk."""

    return db.query(
        """
        SELECT u.user_id, u.username, u.full_name, u.role
        FROM app_user u
        JOIN user_plant_access a ON a.user_id = u.user_id
        WHERE u.active = 1 AND a.plant_id = ? AND u.pin_hash <> ''
        ORDER BY u.full_name
        """,
        (plant_id,),
        conn,
    )


def set_pin(username: str, pin: str, conn=None) -> None:
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise ValidationError(
            "A PIN is 4 to 8 digits.", fields={"pin": "Use 4 to 8 digits."}
        )
    db.update("app_user", {"username": username}, {"pin_hash": hash_password(pin)}, conn)


def logout(token: str, conn=None) -> None:
    db.execute("DELETE FROM user_session WHERE token = ?", (token,), conn)


def user_from_token(token: str | None, conn=None) -> dict:
    if not token:
        raise AuthError("Sign in to continue.")
    row = db.query_one(
        """
        SELECT s.expires_at, u.*
        FROM user_session s
        JOIN app_user u ON u.user_id = s.user_id
        WHERE s.token = ? AND u.active = 1
        """,
        (token,),
        conn,
    )
    if row is None:
        raise AuthError("Session not found — sign in again.")
    expires = parse_dt(row["expires_at"])
    if expires is None or expires < utc_now():
        db.execute("DELETE FROM user_session WHERE token = ?", (token,), conn)
        raise AuthError("Session expired — sign in again.")
    return row


def public_user(user: dict, conn=None) -> dict:
    """The user shape the UI receives — never the password hash."""

    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "full_name": user["full_name"],
        "email": user.get("email", ""),
        "role": user["role"],
        "plants": plants_for_user(user["user_id"], conn),
        "permissions": sorted(ROLE_PERMISSIONS.get(user["role"], set())),
    }


def has_permission(user: dict, permission: str) -> bool:
    granted = ROLE_PERMISSIONS.get(user.get("role", ""), set())
    return "*" in granted or permission in granted


def require_permission(user: dict, permission: str) -> None:
    if not has_permission(user, permission):
        raise PermissionError_(
            f"Your role ({user.get('role')}) cannot {permission.replace('.', ' ')}.",
            permission=permission,
        )


def require_plant(user: dict, plant_id: int, conn=None) -> None:
    """Plant-level access, enforced server-side on every write."""

    if user.get("role") == "admin":
        return
    allowed = {p["plant_id"] for p in plants_for_user(user["user_id"], conn)}
    if plant_id not in allowed:
        raise PermissionError_(
            "You do not have access to that plant.", plant_id=plant_id
        )


def purge_expired_sessions(conn=None) -> int:
    return db.execute(
        "DELETE FROM user_session WHERE expires_at < ?", (utc_now_iso(),), conn
    )
