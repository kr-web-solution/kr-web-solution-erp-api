"""F-032: soft-deleted projects are not operable via API (404)."""

import pytest
from fastapi.testclient import TestClient

from app.core.database import AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.models import Client, Project, User
from main import app


@pytest.fixture
def founder_client():
    async def _fake_user() -> User:
        return User(
            id="usr_test_f032",
            email="founder-f032@example.com",
            password_hash="x",
            full_name="Founder F032",
            role="founder_ceo",
            party_type="staff",
            is_active=True,
        )

    app.dependency_overrides[get_current_user] = _fake_user
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_soft_deleted_project_detail_update_subresource_404(founder_client: TestClient):
    async with AsyncSessionLocal() as session:
        c = Client(name="F032 SoftDelete Client")
        session.add(c)
        await session.flush()
        project = Project(
            name="F032 SoftDelete Project",
            client_id=c.id,
            status="lead",
        )
        session.add(project)
        await session.commit()
        pid = project.id

    async with AsyncSessionLocal() as session:
        project = await session.get(Project, pid)
        assert project is not None
        project.soft_delete()
        await session.commit()

    get_res = founder_client.get(f"/api/v1/projects/{pid}")
    assert get_res.status_code == 404

    patch_res = founder_client.patch(
        f"/api/v1/projects/{pid}",
        json={"name": "Should Not Update"},
    )
    assert patch_res.status_code == 404

    docs_res = founder_client.get(f"/api/v1/projects/{pid}/documents")
    assert docs_res.status_code == 404

    money_res = founder_client.get(f"/api/v1/projects/{pid}/money")
    assert money_res.status_code == 404

    toggle_res = founder_client.post(f"/api/v1/projects/{pid}/maintenance/toggle")
    assert toggle_res.status_code == 404
