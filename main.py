from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.router import api_router
from app.core.config import settings
from app.core.database import init_db
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.middleware import register_middleware
from app.services.scheduler import start_scheduler, stop_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    from app.services.object_storage import ensure_buckets
    ensure_buckets()
    await init_db()
    # Seed development data if database is empty
    from app.scripts.seed import seed_database_async
    await seed_database_async()
    # Start recurring invoices background scheduler
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
