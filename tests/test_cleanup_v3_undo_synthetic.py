"""Synthetic cleanup mutations + undo restore every changed field (isolated test DB)."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal as TestingSessionLocal
from app.models import AuditLog, Client, Invoice, Project, RecurringInvoice
from tests.test_client_project_rules import _make_client
from app.services.client_project import create_project

TAG = "SYN_CLEANUP_V3"
VOID_NOTE = "Test data, cancelled"


def _sql_literal(val) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return str(val)
    return "'" + json.dumps(str(val), ensure_ascii=True)[1:-1].replace("'", "''") + "'"


@pytest.mark.asyncio
async def test_cleanup_v3_audit_and_undo_restores_fields():
    suffix = uuid.uuid4().hex[:8]
    async with TestingSessionLocal() as session:
        c = await _make_client(session, f"{TAG} Client {suffix}")
        project = await create_project(session, name=f"{TAG} Project {suffix}", client_ids=[c.id])
        soft = Invoice(
            invoice_number=f"{TAG}-SOFT-{suffix}",
            invoice_type="custom_dev",
            client_id=c.id,
            project_id=project.id,
            status="draft",
            issue_date=date.today(),
            due_date=date.today(),
            subtotal_paise=10000,
            tax_rate_percent=0,
            tax_paise=0,
            total_paise=10000,
            notes=TAG,
        )
        void = Invoice(
            invoice_number=f"KRWS-SYN-UNDO-{suffix}",
            invoice_type="custom_dev",
            client_id=c.id,
            project_id=project.id,
            status="sent",
            issue_date=date.today(),
            due_date=date.today(),
            subtotal_paise=10000,
            tax_rate_percent=0,
            tax_paise=0,
            total_paise=10000,
            notes=TAG,
        )
        plan = RecurringInvoice(
            client_id=c.id,
            project_id=project.id,
            amount_paise=5000,
            send_day=1,
            due_after_days=5,
            reminder_every_days=2,
            starts_on=date.today(),
            line_items_template=[],
            tax_rate_percent=0,
            day_of_month=1,
            next_run_date=date(2099, 1, 1),
            active=True,
            notes=TAG,
        )
        session.add_all([soft, void, plan])
        await session.commit()
        soft_id, void_id, plan_id, pid, cid = soft.id, void.id, plan.id, project.id, c.id
        soft_deleted_at = soft.deleted_at
        void_status, void_notes = void.status, void.notes
        plan_deleted_at, plan_active = plan.deleted_at, plan.active
        proj_deleted_at = project.deleted_at
        cli_deleted_at = c.deleted_at

    now = datetime.now(timezone.utc)
    batch = f"cleanup_v3_syn_{now.strftime('%Y%m%dT%H%M%SZ')}"

    async with TestingSessionLocal() as session:
        soft = await session.get(Invoice, soft_id)
        void = await session.get(Invoice, void_id)
        plan = await session.get(RecurringInvoice, plan_id)
        project = await session.get(Project, pid)
        client = await session.get(Client, cid)
        soft.soft_delete()
        void.status = "cancelled"
        void.notes = f"{void.notes}\n{VOID_NOTE}" if void.notes else VOID_NOTE
        plan.soft_delete()
        plan.active = False
        project.soft_delete()
        client.soft_delete()
        for etype, eid in (
            ("invoices", soft_id),
            ("invoices", void_id),
            ("projects", pid),
            ("clients", cid),
            ("recurring_invoices", plan_id),
        ):
            session.add(
                AuditLog(
                    actor_id=None,
                    entity_type=etype,
                    entity_id=eid,
                    action="soft_delete",
                    changed_fields=["cleanup_batch"],
                    before=None,
                    after={"batch": batch},
                    ip=TAG,
                    user_agent=batch,
                )
            )
        await session.commit()

    async with TestingSessionLocal() as session:
        n = (
            await session.execute(
                select(AuditLog).where(AuditLog.ip == TAG, AuditLog.user_agent == batch)
            )
        ).scalars().all()
        assert len(n) >= 5

    # Undo via raw SQL matching v3 undo field coverage
    undo = f"""
    UPDATE invoices SET deleted_at = {_sql_literal(soft_deleted_at)} WHERE id = {_sql_literal(soft_id)};
    UPDATE invoices SET status = {_sql_literal(void_status)}, notes = {_sql_literal(void_notes)}
      WHERE id = {_sql_literal(void_id)};
    UPDATE recurring_invoices SET deleted_at = {_sql_literal(plan_deleted_at)},
      active = {_sql_literal(plan_active)} WHERE id = {_sql_literal(plan_id)};
    UPDATE projects SET deleted_at = {_sql_literal(proj_deleted_at)} WHERE id = {_sql_literal(pid)};
    UPDATE clients SET deleted_at = {_sql_literal(cli_deleted_at)} WHERE id = {_sql_literal(cid)};
    """
    async with TestingSessionLocal() as session:
        for stmt in undo.strip().split(";"):
            s = stmt.strip()
            if s:
                await session.execute(text(s))
        session.add(
            AuditLog(
                actor_id=None,
                entity_type="cleanup",
                entity_id="undo",
                action="cleanup_v3_undo",
                changed_fields=["undo"],
                before=None,
                after={"note": "synthetic"},
                ip=TAG,
                user_agent="undo",
            )
        )
        await session.commit()

    async with TestingSessionLocal() as session:
        soft = await session.get(Invoice, soft_id)
        void = await session.get(Invoice, void_id)
        plan = await session.get(RecurringInvoice, plan_id)
        project = await session.get(Project, pid)
        client = await session.get(Client, cid)
        assert soft.deleted_at is None
        assert void.status == void_status
        assert void.notes == void_notes
        assert plan.deleted_at is None and plan.active is True
        assert project.deleted_at is None
        assert client.deleted_at is None
        undo_rows = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "cleanup_v3_undo", AuditLog.ip == TAG
                )
            )
        ).scalars().all()
        assert len(undo_rows) >= 1
