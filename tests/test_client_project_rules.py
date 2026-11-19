"""Business rules: Client ↔ Project ↔ Invoice / MaintenancePlan."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.database import AsyncSessionLocal as TestingSessionLocal
from app.core.exceptions import BusinessRuleError, ConflictException, NotFoundError
from app.models import Client, Invoice, Project, ProjectClient, RecurringInvoice
from app.schemas import ProjectCreate
from app.services.client_project import (
    add_client_to_project,
    create_invoice_guards,
    create_maintenance_plan,
    create_project,
    delete_client,
    delete_project,
    remove_client_from_project,
)


async def _make_client(session, name: str) -> Client:
    c = Client(name=name)
    session.add(c)
    await session.flush()
    return c


@pytest.mark.asyncio
async def test_project_empty_client_list_rejected_by_schema():
    with pytest.raises(ValidationError):
        ProjectCreate(name="No Clients", client_ids=[])


@pytest.mark.asyncio
async def test_project_empty_client_list_rejected_by_service():
    async with TestingSessionLocal() as session:
        with pytest.raises(BusinessRuleError, match="at least one client"):
            await create_project(session, name="X", client_ids=[])
        await session.rollback()


@pytest.mark.asyncio
async def test_remove_last_client_blocked():
    async with TestingSessionLocal() as session:
        a = await _make_client(session, "Rule Client A")
        project = await create_project(session, name="Solo Project", client_ids=[a.id])
        await session.commit()
        pid, aid = project.id, a.id

    async with TestingSessionLocal() as session:
        with pytest.raises(BusinessRuleError, match="Cannot remove the last client"):
            await remove_client_from_project(session, pid, aid)
        await session.rollback()


@pytest.mark.asyncio
async def test_add_and_remove_client_ok_when_two():
    async with TestingSessionLocal() as session:
        a = await _make_client(session, "Multi A")
        b = await _make_client(session, "Multi B")
        project = await create_project(session, name="Multi Project", client_ids=[a.id])
        await add_client_to_project(session, project.id, b.id)
        await session.commit()
        pid, aid, bid = project.id, a.id, b.id

    async with TestingSessionLocal() as session:
        await remove_client_from_project(session, pid, aid)
        await session.commit()

    async with TestingSessionLocal() as session:
        rows = (
            await session.execute(
                select(ProjectClient).where(ProjectClient.project_id == pid)
            )
        ).scalars().all()
        assert [r.client_id for r in rows] == [bid]


@pytest.mark.asyncio
async def test_delete_project_blocked_with_invoices_and_plans():
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "Billable Client")
        project = await create_project(session, name="Billable Project", client_ids=[c.id])
        inv = Invoice(
            invoice_number=f"TEST-DEL-{project.id[-6:]}",
            invoice_type="custom_dev",
            client_id=c.id,
            project_id=project.id,
            status="draft",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=7),
            subtotal_paise=10000,
            tax_rate_percent=0,
            tax_paise=0,
            total_paise=10000,
        )
        session.add(inv)
        await session.flush()
        plan = await create_maintenance_plan(
            session,
            project_id=project.id,
            client_id=c.id,
            amount_paise=50000,
            next_run_date=date(2099, 1, 1),
        )
        await session.commit()
        pid, iid, plan_id = project.id, inv.id, plan.id

    async with TestingSessionLocal() as session:
        with pytest.raises(ConflictException) as exc:
            await delete_project(session, pid)
        assert exc.value.extra.get("invoice_count", 0) >= 1
        assert exc.value.extra.get("plan_count", 0) >= 1
        assert "Cannot delete project" in exc.value.detail
        await session.rollback()

    # Cleanup then delete succeeds
    async with TestingSessionLocal() as session:
        inv = (await session.get(Invoice, iid))
        plan = (await session.get(RecurringInvoice, plan_id))
        if inv:
            inv.soft_delete()
        if plan:
            plan.soft_delete()
        await session.commit()

    async with TestingSessionLocal() as session:
        await delete_project(session, pid)
        await session.commit()

    async with TestingSessionLocal() as session:
        p = await session.get(Project, pid)
        assert p is not None
        assert p.deleted_at is not None


@pytest.mark.asyncio
async def test_delete_sole_client_blocked():
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "Sole Only")
        project = await create_project(session, name="Sole Owner Project", client_ids=[c.id])
        await session.commit()
        cid, pname = c.id, project.name

    async with TestingSessionLocal() as session:
        with pytest.raises(ConflictException) as exc:
            await delete_client(session, cid)
        assert pname in exc.value.detail
        await session.rollback()


@pytest.mark.asyncio
async def test_invoice_rejects_client_not_on_project():
    async with TestingSessionLocal() as session:
        a = await _make_client(session, "On Project")
        b = await _make_client(session, "Off Project")
        project = await create_project(session, name="Membership Project", client_ids=[a.id])
        await session.commit()
        pid, bid = project.id, b.id

    async with TestingSessionLocal() as session:
        with pytest.raises(BusinessRuleError, match="Client is not part of this project"):
            await create_invoice_guards(session, project_id=pid, client_id=bid)
        await session.rollback()


@pytest.mark.asyncio
async def test_invoice_rejects_missing_project():
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "No Project Client")
        await session.commit()
        with pytest.raises(BusinessRuleError, match="project_id is required"):
            await create_invoice_guards(session, project_id=None, client_id=c.id)
        await session.rollback()


@pytest.mark.asyncio
async def test_invoice_rejects_unknown_project():
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "Ghost Project Client")
        await session.commit()
        with pytest.raises(NotFoundError, match="Project not found"):
            await create_invoice_guards(session, project_id="prj_does_not_exist", client_id=c.id)
        await session.rollback()


@pytest.mark.asyncio
async def test_delete_project_ignores_soft_deleted_invoices_and_plans():
    """Rule 6b: only live (non-soft-deleted) children block delete_project."""
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "SoftChild Client")
        project = await create_project(session, name="SoftChild Project", client_ids=[c.id])
        inv = Invoice(
            invoice_number=f"TEST-SOFTCHILD-{project.id[-6:]}",
            invoice_type="custom_dev",
            client_id=c.id,
            project_id=project.id,
            status="draft",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=7),
            subtotal_paise=1000,
            tax_rate_percent=0,
            tax_paise=0,
            total_paise=1000,
        )
        session.add(inv)
        await session.flush()
        plan = await create_maintenance_plan(
            session,
            project_id=project.id,
            client_id=c.id,
            amount_paise=1000,
            next_run_date=date(2099, 1, 1),
        )
        await session.commit()
        pid, iid, plan_id = project.id, inv.id, plan.id

    async with TestingSessionLocal() as session:
        inv = await session.get(Invoice, iid)
        plan = await session.get(RecurringInvoice, plan_id)
        inv.soft_delete()
        plan.soft_delete()
        await session.commit()

    async with TestingSessionLocal() as session:
        # Must succeed — soft-deleted children must not count
        await delete_project(session, pid)
        await session.commit()

    async with TestingSessionLocal() as session:
        p = await session.get(Project, pid)
        assert p.deleted_at is not None


@pytest.mark.asyncio
async def test_create_client_without_project():
    """Rule 1: clients may exist with zero projects."""
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "Orphan Client OK")
        await session.commit()
        cid = c.id
    async with TestingSessionLocal() as session:
        c = await session.get(Client, cid)
        assert c is not None
        n = (
            await session.execute(
                select(ProjectClient).where(ProjectClient.client_id == cid)
            )
        ).scalars().all()
        assert n == []


@pytest.mark.asyncio
async def test_add_client_duplicate_is_idempotent():
    """Rule 3: adding an existing membership is a no-op (no duplicate row)."""
    async with TestingSessionLocal() as session:
        a = await _make_client(session, "Dup A")
        project = await create_project(session, name="Dup Project", client_ids=[a.id])
        await session.commit()
        pid, aid = project.id, a.id
    async with TestingSessionLocal() as session:
        await add_client_to_project(session, pid, aid)
        await add_client_to_project(session, pid, aid)
        await session.commit()
    async with TestingSessionLocal() as session:
        rows = (
            await session.execute(
                select(ProjectClient).where(ProjectClient.project_id == pid)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].client_id == aid


@pytest.mark.asyncio
async def test_delete_project_leaves_clients_untouched():
    """Rule 7 follow-up: after project soft-delete, client row remains."""
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "Survive Client")
        project = await create_project(session, name="Temp Project", client_ids=[c.id])
        await session.commit()
        pid, cid = project.id, c.id
    async with TestingSessionLocal() as session:
        await delete_project(session, pid)
        await session.commit()
    async with TestingSessionLocal() as session:
        c = await session.get(Client, cid)
        assert c is not None
        assert c.deleted_at is None
        p = await session.get(Project, pid)
        assert p.deleted_at is not None


@pytest.mark.asyncio
async def test_plan_rejects_client_not_on_project():
    """Rule 8 for maintenance plans."""
    async with TestingSessionLocal() as session:
        a = await _make_client(session, "Plan On")
        b = await _make_client(session, "Plan Off")
        project = await create_project(session, name="Plan Project", client_ids=[a.id])
        await session.commit()
        pid, bid = project.id, b.id
    async with TestingSessionLocal() as session:
        with pytest.raises(BusinessRuleError, match="Client is not part of this project"):
            await create_maintenance_plan(
                session,
                project_id=pid,
                client_id=bid,
                amount_paise=1000,
                next_run_date=date(2099, 1, 1),
            )
        await session.rollback()


@pytest.mark.asyncio
async def test_plan_requires_existing_project():
    """Rule 6 for plans."""
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "Plan No Project")
        await session.commit()
        with pytest.raises(NotFoundError, match="Project not found"):
            await create_maintenance_plan(
                session,
                project_id="prj_missing_plan",
                client_id=c.id,
                amount_paise=1000,
                next_run_date=date(2099, 1, 1),
            )
        await session.rollback()
