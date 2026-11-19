"""Shared project visibility filters (client ↔ project M2M)."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Project, ProjectClient


def projects_visible_to_client(client_id: str):
    """Projects linked to ``client_id`` via ``project_clients``.

    Prefer this over ``Project.client_id`` so secondary clients match the
    projects list. Uses an IN subquery so counts are not duplicated.
    """
    return Project.id.in_(
        select(ProjectClient.project_id).where(ProjectClient.client_id == client_id)
    )


async def client_is_on_project(
    db: AsyncSession, project_id: str, client_id: Optional[str]
) -> bool:
    """True when ``client_id`` has a ``project_clients`` row for this project."""
    if not client_id:
        return False
    row = (
        await db.execute(
            select(ProjectClient.project_id).where(
                ProjectClient.project_id == project_id,
                ProjectClient.client_id == client_id,
            )
        )
    ).scalar_one_or_none()
    return row is not None
