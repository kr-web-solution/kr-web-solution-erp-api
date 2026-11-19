from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


class AppException(Exception):
    def __init__(
        self,
        detail: str,
        *,
        status_code: int = 400,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.detail = detail
        self.status_code = status_code
        self.extra = extra or {}


class BusinessRuleError(AppException):
    """Readable domain-rule violation (typically 400)."""

    def __init__(self, detail: str, *, status_code: int = 400, **extra: Any) -> None:
        super().__init__(detail, status_code=status_code, extra=extra)


class NotFoundError(AppException):
    def __init__(self, detail: str = "Resource not found", **extra: Any) -> None:
        super().__init__(detail, status_code=404, extra=extra)


class CredentialsException(AppException):
    def __init__(self, detail: str = "Could not validate credentials") -> None:
        super().__init__(detail, status_code=401)


class PermissionDeniedException(AppException):
    def __init__(self, detail: str = "Not enough permissions") -> None:
        super().__init__(detail, status_code=403)


class NotFoundException(AppException):
    def __init__(self, resource: str = "Resource") -> None:
        super().__init__(f"{resource} not found", status_code=404)


class ConflictException(AppException):
    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail, status_code=409, extra=extra)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(_: Request, exc: AppException) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        body: Dict[str, Any] = {"detail": exc.detail}
        if exc.extra:
            body.update(exc.extra)
        return JSONResponse(
            status_code=exc.status_code,
            content=body,
            headers=headers,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(_: Request, exc: IntegrityError) -> JSONResponse:
        import logging

        logging.getLogger(__name__).warning("IntegrityError: %s", getattr(exc, "orig", None) or exc)
        return JSONResponse(
            status_code=409,
            content={"detail": "Operation conflicts with existing data constraints"},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        # Return JSON so browsers don't surface opaque "Failed to fetch" on 500s
        import logging

        logging.getLogger(__name__).exception("Unhandled API error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc) or "Internal server error"},
        )
