"""Typed application errors and the handlers that render them.

Every error response shares one envelope so clients can parse failures
uniformly::

    {"error": {"code": "book_not_found", "message": "...", "details": {...}},
     "request_id": "..."}
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging_config import get_logger, request_id_ctx

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for errors that map cleanly onto an HTTP response."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource was not found."


class ValidationError(AppError):
    # Literal 422 rather than status.HTTP_422_*: Starlette renamed the
    # constant (ENTITY -> CONTENT) and the old name warns on newer versions.
    status_code = 422
    code = "validation_error"
    message = "The request payload is invalid."


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_failed"
    message = "Could not validate credentials."


class PermissionError_(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    message = "You do not have access to this resource."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The resource already exists or has been modified."


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Please slow down."


class ExternalServiceError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "external_service_unavailable"
    message = "An upstream service is unavailable. Please retry shortly."


def _serializable_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Render Pydantic's error list as JSON-safe dicts.

    When a custom ``field_validator`` raises ValueError, Pydantic puts the
    exception *object* in ``error["ctx"]["error"]`` and ``error["input"]`` can
    be any raw payload value. Passing those straight to JSONResponse raises
    "Object of type ValueError is not JSON serializable" — turning every
    custom-validator failure into a 500 instead of a 422.
    """
    cleaned: list[dict[str, Any]] = []
    for error in exc.errors():
        item = {
            "field": ".".join(str(part) for part in error.get("loc", ())),
            "type": error.get("type"),
            "message": error.get("msg"),
        }
        # `ctx` carries useful bounds (limit_value, max_length) alongside the
        # unserialisable exception — keep the parts that survive json.dumps.
        if ctx := error.get("ctx"):
            safe_ctx = {
                key: value
                for key, value in ctx.items()
                if isinstance(value, (str, int, float, bool, type(None)))
            }
            if safe_ctx:
                item["context"] = safe_ctx
        cleaned.append(item)
    return cleaned


def _envelope(
    code: str, message: str, details: dict | None = None
) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": details or {}},
        "request_id": request_id_ctx.get(),
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        # 5xx is our bug; 4xx is the caller's. Log accordingly.
        log = logger.error if exc.status_code >= 500 else logger.info
        log("%s: %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(f"http_{exc.status_code}", str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "validation_error",
                "One or more fields failed validation.",
                {"fields": _serializable_errors(exc)},
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError):
        logger.exception("Database error: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_envelope("database_error", "A database error occurred."),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope("internal_error", "An unexpected error occurred."),
        )
