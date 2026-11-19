"""Isolated-DB tests for project_clients drift query + idempotent repair."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal as TestingSessionLocal
from app.models import Project, ProjectClient
from tests.test_client_project_rules import _make_client


@pytest.mark.asyncio
async def test_drift_detects_and_repair_is_idempotent():
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "Drift Client")
        # Intentionally create project WITHOUT project_clients (drift)
        p = Project(name="Drift Project", client_id=c.id, status="running", budget_paise=0)
        session.add(p)
        await session.commit()
        pid, cid = p.id, c.id

    async with TestingSessionLocal() as session:
        n = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM projects p
                    WHERE p.id = :pid AND p.deleted_at IS NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM project_clients pc WHERE pc.project_id = p.id
                      )
                    """
                ),
                {"pid": pid},
            )
        ).scalar()
        assert n == 1

    async with TestingSessionLocal() as session:
        await session.execute(
            text(
                """
                INSERT INTO project_clients (project_id, client_id, is_primary)
                VALUES (:pid, :cid, true)
                ON CONFLICT DO NOTHING
                """
            ),
            {"pid": pid, "cid": cid},
        )
        await session.commit()

    async with TestingSessionLocal() as session:
        n2 = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM projects p
                    WHERE p.id = :pid AND p.deleted_at IS NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM project_clients pc WHERE pc.project_id = p.id
                      )
                    """
                ),
                {"pid": pid},
            )
        ).scalar()
        assert n2 == 0
        # second insert does not duplicate
        await session.execute(
            text(
                """
                INSERT INTO project_clients (project_id, client_id, is_primary)
                VALUES (:pid, :cid, true)
                ON CONFLICT DO NOTHING
                """
            ),
            {"pid": pid, "cid": cid},
        )
        await session.commit()
        rows = (
            await session.execute(
                select(ProjectClient).where(ProjectClient.project_id == pid)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].client_id == cid
        assert rows[0].is_primary is True
