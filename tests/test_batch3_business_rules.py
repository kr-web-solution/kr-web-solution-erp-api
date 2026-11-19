"""Batch 3: F-003 invoice PATCH lock + soft-delete 404 gaps + concurrency."""

import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.database import AsyncSessionLocal as TestingSessionLocal
from app.core.exceptions import BusinessRuleError
from app.models import Engagement, Invoice, ProjectClient
from app.schemas import InvoiceUpdate
from app.services.client_project import (
    create_project,
    remove_client_from_project,
)
from tests.test_client_project_rules import _make_client


def test_invoice_update_schema_rejects_project_id_and_client_id():
    with pytest.raises(ValidationError):
        InvoiceUpdate(project_id="prj_x")
    with pytest.raises(ValidationError):
        InvoiceUpdate(client_id="cli_x")
    # Allowed field still works
    ok = InvoiceUpdate(notes="hello")
    assert ok.notes == "hello"


@pytest.mark.asyncio
async def test_invoice_patch_rejects_project_id_via_api(client: TestClient, founder_cookies):
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "F003 Client")
        p = await create_project(session, name="F003 Project", client_ids=[c.id])
        inv = Invoice(
            invoice_number=f"F003-{p.id[-6:]}",
            invoice_type="project",
            client_id=c.id,
            project_id=p.id,
            issue_date=date.today(),
            due_date=date.today(),
            subtotal_paise=1000,
            tax_rate_percent=0,
            tax_paise=0,
            total_paise=1000,
            status="draft",
        )
        session.add(inv)
        await session.commit()
        inv_id = inv.id

    res = client.patch(
        f"/api/v1/invoices/{inv_id}",
        json={"project_id": "prj_other"},
        cookies=founder_cookies,
    )
    assert res.status_code in (400, 422), res.text


@pytest.mark.asyncio
async def test_soft_deleted_invoice_and_engagement_404(client: TestClient, founder_cookies):
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "SoftDel Billing Client")
        p = await create_project(session, name="SoftDel Billing Project", client_ids=[c.id])
        inv = Invoice(
            invoice_number=f"SOFT-{p.id[-6:]}",
            invoice_type="project",
            client_id=c.id,
            project_id=p.id,
            issue_date=date.today(),
            due_date=date.today(),
            subtotal_paise=500,
            tax_rate_percent=0,
            tax_paise=0,
            total_paise=500,
            status="draft",
        )
        session.add(inv)
        eng = Engagement(
            title="SoftDel Engagement",
            company_name="SoftDel Co",
            contact_name="Contact",
            client_id=c.id,
            current_stage=1,
            status="active",
        )
        session.add(eng)
        await session.flush()
        inv.soft_delete()
        eng.soft_delete()
        await session.commit()
        inv_id, eng_id = inv.id, eng.id

    inv_res = client.get(f"/api/v1/invoices/{inv_id}", cookies=founder_cookies)
    assert inv_res.status_code == 404

    eng_res = client.get(f"/api/v1/engagements/{eng_id}", cookies=founder_cookies)
    assert eng_res.status_code == 404


@pytest.mark.asyncio
async def test_concurrent_last_client_removal_blocked():
    """Two real PostgreSQL sessions: concurrent last-two removals leave ≥1 client.

    Relies on SELECT … FOR UPDATE in ``remove_client_from_project``.
    """
    async with TestingSessionLocal() as session:
        a = await _make_client(session, "Conc A")
        b = await _make_client(session, "Conc B")
        project = await create_project(session, name="Conc Project", client_ids=[a.id, b.id])
        await session.commit()
        pid, aid, bid = project.id, a.id, b.id

    async def remove_one(cid: str):
        async with TestingSessionLocal() as session:
            try:
                await remove_client_from_project(session, pid, cid)
                await session.commit()
                return "ok"
            except BusinessRuleError:
                await session.rollback()
                return "blocked"
            except Exception:
                await session.rollback()
                raise

    results = await asyncio.gather(remove_one(aid), remove_one(bid))
    assert "ok" in results
    assert "blocked" in results or results.count("ok") == 1
    async with TestingSessionLocal() as session:
        rows = (
            await session.execute(
                select(ProjectClient).where(ProjectClient.project_id == pid)
            )
        ).scalars().all()
        assert len(rows) >= 1, f"FOR UPDATE failed: {len(rows)} clients left; results={results}"


@pytest.mark.asyncio
async def test_concurrent_removal_without_for_update_can_wipe_clients(monkeypatch):
    """Proof: if SELECT … FOR UPDATE is removed, two sessions can leave 0 clients.

    Uses a delayed unlocked path that mirrors the old race (check then delete).
    """
    from sqlalchemy import delete
    from app.models import Project
    from app.services import client_project as cp

    async with TestingSessionLocal() as session:
        a = await _make_client(session, "Race A")
        b = await _make_client(session, "Race B")
        project = await create_project(session, name="Race Project", client_ids=[a.id, b.id])
        await session.commit()
        pid, aid, bid = project.id, a.id, b.id

    async def unlocked_remove(db, project_id: str, client_id: str):
        # Intentionally NO for_update — demonstrates why the lock is required.
        project = (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalars().first()
        rows = (
            await db.execute(
                select(ProjectClient).where(ProjectClient.project_id == project_id)
            )
        ).scalars().all()
        current_ids = [r.client_id for r in rows]
        if client_id not in current_ids:
            raise BusinessRuleError("not on project")
        if len(current_ids) <= 1:
            raise BusinessRuleError("Cannot remove the last client from a project")
        await asyncio.sleep(0.15)  # widen race window
        await db.execute(
            delete(ProjectClient).where(
                ProjectClient.project_id == project_id,
                ProjectClient.client_id == client_id,
            )
        )
        await db.flush()
        return project

    monkeypatch.setattr(cp, "remove_client_from_project", unlocked_remove)

    async def remove_one(cid: str):
        async with TestingSessionLocal() as session:
            try:
                await cp.remove_client_from_project(session, pid, cid)
                await session.commit()
                return "ok"
            except BusinessRuleError:
                await session.rollback()
                return "blocked"
            except Exception:
                await session.rollback()
                raise

    # Retry a few times — races are probabilistic without a lock
    wiped = False
    for _ in range(8):
        async with TestingSessionLocal() as session:
            # reset membership to two clients
            await session.execute(
                delete(ProjectClient).where(ProjectClient.project_id == pid)
            )
            session.add(ProjectClient(project_id=pid, client_id=aid, is_primary=True))
            session.add(ProjectClient(project_id=pid, client_id=bid, is_primary=False))
            await session.commit()

        results = await asyncio.gather(remove_one(aid), remove_one(bid))
        async with TestingSessionLocal() as session:
            n = len(
                (
                    await session.execute(
                        select(ProjectClient).where(ProjectClient.project_id == pid)
                    )
                ).scalars().all()
            )
        if n == 0 and results.count("ok") == 2:
            wiped = True
            break

    assert wiped, (
        "Expected unlocked concurrent removals to leave 0 clients at least once; "
        "FOR UPDATE is required to prevent this race"
    )
