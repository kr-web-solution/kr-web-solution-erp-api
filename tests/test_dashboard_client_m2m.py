"""F-033: dashboard/list client project visibility uses project_clients (M2M)."""

import pytest
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal, not_deleted
from app.models import Client, Project, ProjectClient
from app.services.project_visibility import projects_visible_to_client


@pytest.mark.asyncio
async def test_secondary_client_sees_running_project_in_visibility_filter():
    async with AsyncSessionLocal() as session:
        primary = Client(name="F033 Primary")
        secondary = Client(name="F033 Secondary")
        session.add_all([primary, secondary])
        await session.flush()
        project = Project(
            name="F033 Shared Running",
            client_id=primary.id,
            status="running",
        )
        session.add(project)
        await session.flush()
        session.add(ProjectClient(project_id=project.id, client_id=primary.id))
        session.add(ProjectClient(project_id=project.id, client_id=secondary.id))
        await session.commit()
        pid, sec_id, pri_id = project.id, secondary.id, primary.id

    async with AsyncSessionLocal() as session:
        p = await session.get(Project, pid)
        assert p is not None
        assert p.client_id == pri_id
        assert p.client_id != sec_id

        count_sec = (
            await session.scalar(
                select(func.count())
                .select_from(Project)
                .where(
                    projects_visible_to_client(sec_id),
                    Project.status == "running",
                    not_deleted(Project),
                )
            )
        ) or 0
        assert count_sec >= 1

        count_pri = (
            await session.scalar(
                select(func.count())
                .select_from(Project)
                .where(
                    projects_visible_to_client(pri_id),
                    Project.status == "running",
                    not_deleted(Project),
                )
            )
        ) or 0
        assert count_pri >= 1

        count_dup_check = (
            await session.scalar(
                select(func.count())
                .select_from(Project)
                .where(projects_visible_to_client(sec_id), Project.id == pid)
            )
        ) or 0
        assert count_dup_check == 1
