"""Domain-level exceptions mapped to HTTP status codes by the API layer.

Keeping these free of FastAPI imports lets the service and domain layers stay
framework-agnostic and unit-testable without an HTTP client.
"""
from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"


class UnbalancedEntryError(ValidationError):
    code = "unbalanced_entry"


class PeriodClosedError(ConflictError):
    code = "period_closed"


class ImmutableRecordError(ConflictError):
    code = "immutable_record"
