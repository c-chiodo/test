"""Domain errors.

Every failure the API can return deliberately carries a stable ``code`` so a
support ticket can quote it and a runbook entry can be written against it. The
legacy client surfaced raw SQL errors (and, in the matrix feature, swallowed
them entirely into blank cells), which made most issues unreportable.
"""

from __future__ import annotations

from typing import Any


class PimsError(Exception):
    """Base class. ``status`` maps to the HTTP response code."""

    code = "pims_error"
    status = 400

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class NotFound(PimsError):
    code = "not_found"
    status = 404


class ValidationError(PimsError):
    """The request cannot be applied as written."""

    code = "validation_error"
    status = 422


class BusinessRuleError(PimsError):
    """The request is well-formed but the operation is not allowed right now."""

    code = "business_rule"
    status = 409


class AuthError(PimsError):
    code = "unauthenticated"
    status = 401


class PermissionError_(PimsError):
    code = "forbidden"
    status = 403


class IntegrationError(PimsError):
    """An upstream system (LIMS, ERP, directory) failed or is stale."""

    code = "integration_error"
    status = 502
