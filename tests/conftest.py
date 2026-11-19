"""Test fixtures: isolated test DB settings + seeded auth users (no shared seed passwords)."""

from __future__ import annotations

import os

# --- Hard safety gate (must run before importing app / opening any DB) ---
# Tests ignore .env DATABASE_URL entirely. TEST_DATABASE_URL is required
# (no password defaults in code). A polluted TEST_DATABASE_URL pointing at a
# non-test DB name is aborted by assert_safe_test_database.
TEST_DB_URL = (os.environ.get("TEST_DATABASE_URL") or "").strip()
if not TEST_DB_URL:
    raise SystemExit(
        "TEST SAFETY ABORT: set TEST_DATABASE_URL to a dedicated local test database "
        "(name must contain 'test' or 'pytest'). Example: "
        "postgresql+asyncpg://USER:PASSWORD@localhost:5432/kr-web-solution-erp-test"
    )

# Force local/test env so Settings validators and the guard stay consistent.
os.environ["ENVIRONMENT"] = "test"
os.environ["PRODUCTION"] = "false"
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["FORCE_TEST_OUTBOUND"] = "1"

from tests.db_safety import UnsafeTestDatabaseError, assert_safe_test_database  # noqa: E402

try:
    _SAFE_DB_NAME = assert_safe_test_database(
        database_url=TEST_DB_URL,
        environment=os.environ.get("ENVIRONMENT"),
        production_flag=os.environ.get("PRODUCTION"),
    )
except UnsafeTestDatabaseError as exc:
    raise SystemExit(f"TEST SAFETY ABORT: {exc}") from exc

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import settings  # noqa: E402

settings.DATABASE_URL = TEST_DB_URL
settings.ENVIRONMENT = "test"
settings.PRODUCTION = False

import app.core.database as db_module  # noqa: E402

db_module.engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
db_module.AsyncSessionLocal = async_sessionmaker(
    bind=db_module.engine, class_=AsyncSession, expire_on_commit=False
)
TestingSessionLocal = db_module.AsyncSessionLocal

from app.core.security import get_password_hash  # noqa: E402
from app.core.roles_catalog import ROLE_CLIENT, ROLE_FOUNDER, ROLE_WORKER  # noqa: E402
from app.models import Client, Project, ProjectClient, Settings, User  # noqa: E402
from app.services.user_identity import apply_role_to_user, ensure_roles_seeded  # noqa: E402
from main import app  # noqa: E402

# Known passwords for API role tests — created by ensure_test_users, only in the test DB
TEST_PASSWORD = "TestPass!2026"
TEST_USERS = {
    "founder": {
        "email": "test-founder@example.com",
        "password": TEST_PASSWORD,
        "full_name": "Test Founder",
        "role": ROLE_FOUNDER,
    },
    "worker": {
        "email": "test-worker@example.com",
        "password": TEST_PASSWORD,
        "full_name": "Test Worker",
        "role": ROLE_WORKER,
    },
    "client": {
        "email": "test-client@example.com",
        "password": TEST_PASSWORD,
        "full_name": "Test Client User",
        "role": ROLE_CLIENT,
    },
}


async def ensure_test_users() -> dict:
    """Upsert founder/worker/client users + a client org. Idempotent. Test DB only."""
    assert_safe_test_database(
        database_url=TEST_DB_URL,
        environment="test",
        production_flag="false",
    )
    async with TestingSessionLocal() as session:
        await ensure_roles_seeded(session)

        st = (
            await session.execute(select(Settings).where(Settings.id == "default"))
        ).scalar_one_or_none()
        if not st:
            session.add(
                Settings(
                    id="default",
                    company_name="KR Web Solution",
                    invoice_prefix="KR",
                    invoice_next_number=1,
                    invoice_sequences={},
                )
            )
            await session.flush()

        client_org = (
            await session.execute(select(Client).where(Client.id == "cli_test_org"))
        ).scalars().first()
        if not client_org:
            client_org = Client(id="cli_test_org", name="Test Client Org")
            session.add(client_org)
            await session.flush()
        elif getattr(client_org, "deleted_at", None) is not None:
            client_org.deleted_at = None

        now = datetime.now(timezone.utc)
        created: dict[str, User] = {}
        for key, spec in TEST_USERS.items():
            email = spec["email"].lower()
            client_id = client_org.id if spec["role"] == ROLE_CLIENT else None
            user = (
                await session.execute(select(User).where(User.email == email))
            ).scalars().first()
            if not user:
                user = User(
                    email=email,
                    password_hash=get_password_hash(spec["password"]),
                    full_name=spec["full_name"],
                    is_active=True,
                    password_set_at=now,
                    profile_completed_at=now,
                )
                apply_role_to_user(user, spec["role"], client_id=client_id)
                session.add(user)
                await session.flush()
            else:
                user.password_hash = get_password_hash(spec["password"])
                user.is_active = True
                user.deleted_at = None
                user.password_set_at = now
                user.profile_completed_at = now
                apply_role_to_user(user, spec["role"], client_id=client_id)
            created[key] = user

        await session.commit()
        return {
            "client_org_id": client_org.id,
            "users": {k: u.id for k, u in created.items()},
            "database": _SAFE_DB_NAME,
        }


def get_auth_cookies(client: TestClient, email: str, password: str):
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, f"Login failed for {email}: {res.text}"
    return res.cookies


async def make_project_with_client(
    session: AsyncSession,
    *,
    name: str,
    client_id: str,
    status: str = "running",
    budget_paise: int = 0,
    **extra,
) -> Project:
    """Create a project and matching project_clients row (tests must not skip M2M)."""
    project = Project(
        name=name,
        client_id=client_id,
        status=status,
        budget_paise=budget_paise,
        **extra,
    )
    session.add(project)
    await session.flush()
    session.add(ProjectClient(project_id=project.id, client_id=client_id, is_primary=True))
    await session.flush()
    return project


@pytest.fixture(scope="session", autouse=True)
def _seed_test_auth():
    import asyncio

    asyncio.run(ensure_test_users())


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _clear_client_cookies(client: TestClient):
    """Prevent auth cookies leaking across tests on the session-scoped client."""
    client.cookies.clear()
    yield
    client.cookies.clear()


@pytest.fixture
def founder_cookies(client: TestClient):
    spec = TEST_USERS["founder"]
    return get_auth_cookies(client, spec["email"], spec["password"])


@pytest.fixture
def worker_cookies(client: TestClient):
    spec = TEST_USERS["worker"]
    return get_auth_cookies(client, spec["email"], spec["password"])


@pytest.fixture
def client_user_cookies(client: TestClient):
    spec = TEST_USERS["client"]
    return get_auth_cookies(client, spec["email"], spec["password"])
