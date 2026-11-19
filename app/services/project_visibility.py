"""Shared project visibility filters (client ↔ project M2M)."""

from sqlalchemy import select

from ..models import Project, ProjectClient


def projects_visible_to_client(client_id: str):
    """Projects linked to ``client_id`` via ``project_clients``.

    Prefer this over ``Project.client_id`` so secondary clients match the
    projects list. Uses an IN subquery so counts are not duplicated.
    """
    return Project.id.in_(
        select(ProjectClient.project_id).where(ProjectClient.client_id == client_id)
    )
