from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.router import api_router
from app.core.config import settings
from app.core.database import init_db
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.middleware import register_middleware
from app.services.scheduler import start_scheduler, stop_scheduler, log_scheduler_startup_status

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    from app.services.object_storage import ensure_buckets
    ensure_buckets()
    # Production/deploy: schema via alembic one-off; data via CLI only (not on startup).
    await init_db()
    log_scheduler_startup_status()
    start_scheduler()
    yield
    stop_scheduler()

def create_application() -> FastAPI:
    application = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.API_VERSION,
        openapi_url=f"{settings.API_V1_STR}/openapi.json" if not settings.PRODUCTION else None,
        docs_url="/docs" if not settings.PRODUCTION else None,
        redoc_url="/redoc" if not settings.PRODUCTION else None,
        lifespan=lifespan,
    )
    register_middleware(application)
    register_exception_handlers(application)
    application.include_router(api_router)
    return application

app = create_application()

if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
