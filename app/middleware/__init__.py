import time
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from ..core.config import settings

logger = logging.getLogger("kr.api")

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.perf_counter()
        method = request.method
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"

        # Log incoming request
        logger.info(f"--> {method} {path} from {client_ip}")

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000
            status_code = response.status_code

            if status_code < 400:
                logger.info(f"<-- {method} {path} {status_code} ({duration_ms:.1f}ms)")
            elif status_code < 500:
                logger.warning(f"<-- {method} {path} {status_code} ({duration_ms:.1f}ms)")
            else:
                logger.error(f"<-- {method} {path} {status_code} ({duration_ms:.1f}ms)")

            return response
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error(f"<-- {method} {path} 500 EXCEPTION ({duration_ms:.1f}ms): {exc}", exc_info=True)
            raise

def register_middleware(app: FastAPI) -> None:
    # Innermost: Request logger
    app.add_middleware(RequestLoggingMiddleware)

    # Outermost: CORS handler
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


