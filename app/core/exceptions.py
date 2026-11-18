from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

class AppException(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        self.detail = detail
        self.status_code = status_code

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
    def __init__(self, detail: str) -> None:
        super().__init__(detail, status_code=409)

def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(_: Request, exc: AppException) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers,
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

