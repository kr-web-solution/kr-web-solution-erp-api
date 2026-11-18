import os
import pytest
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from fastapi.testclient import TestClient

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:udhaya@localhost:5432/kr-web-solution-erp-test"
)

# Enforce test database in settings
os.environ["DATABASE_URL"] = TEST_DB_URL

from app.core.config import settings
settings.DATABASE_URL = TEST_DB_URL

# Re-initialize database engine to point to test DB with NullPool
import app.core.database as db_module
db_module.engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
db_module.AsyncSessionLocal = async_sessionmaker(bind=db_module.engine, class_=AsyncSession, expire_on_commit=False)
TestingSessionLocal = db_module.AsyncSessionLocal

from main import app

@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client
