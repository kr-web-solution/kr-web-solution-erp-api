"""Seed all lookup catalogs (idempotent)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import lookups_catalog as cat
from ..core.roles_catalog import ROLE_SEED
from ..models import Role
from ..models_lookups import (
    PartyType,
    InvoiceLedger,
    InvoiceType,
    InvoiceStatus,
    EmailDeliveryStatus,
    ProjectType,
    ProjectStatus,
    PipelineStage,
    EngagementKind,
    EngagementStatus,
    EnquirySource,
    LostReason,
    LeadStatus,
    LeadSource,
    DocumentKind,
    FeatureStatus,
    LeaveType,
    ApprovalStatus,
    WorkTaskStatus,
    ExpenseSection,
    ExpenseCategory,
    ExpenseClaimCategory,
    LateFeeType,
    LateFeeStatus,
    AttendanceCorrectionReason,
)


async def _upsert(session: AsyncSession, model, rows: list[dict], code_key: str = "code") -> dict[str, str]:
    """Insert missing rows by code (or code+scope). Returns code → id map."""
    existing = {
        getattr(r, code_key): r
        for r in (await session.execute(select(model))).scalars().all()
    }
    # DocumentKind uses code+scope
    if model is DocumentKind:
        existing_pair = {
            (r.code, r.scope): r
            for r in (await session.execute(select(model))).scalars().all()
        }
        for row in rows:
            key = (row["code"], row["scope"])
            if key in existing_pair:
                obj = existing_pair[key]
                for k, v in row.items():
                    if k != "id" and hasattr(obj, k):
                        setattr(obj, k, v)
            else:
                session.add(model(**{k: v for k, v in row.items() if hasattr(model, k) or k in model.__table__.c}))
        await session.flush()
        return {
            f"{r.code}:{r.scope}": r.id
            for r in (await session.execute(select(model))).scalars().all()
        }

    id_by_code: dict[str, str] = {}
    for row in rows:
        code = row[code_key]
        data = {k: v for k, v in row.items() if k in model.__table__.c.keys()}
        if code in existing:
            obj = existing[code]
            for k, v in data.items():
                if k != "id":
                    setattr(obj, k, v)
            id_by_code[code] = obj.id
        else:
            obj = model(**data)
            session.add(obj)
            id_by_code[code] = data.get("id") or obj.id
    await session.flush()
    # refresh ids
    for r in (await session.execute(select(model))).scalars().all():
        id_by_code[r.code] = r.id
    return id_by_code


async def seed_all_lookups(session: AsyncSession) -> None:
    party_ids = await _upsert(session, PartyType, cat.PARTY_TYPES)
    ledger_ids = await _upsert(session, InvoiceLedger, cat.INVOICE_LEDGERS)

    inv_type_rows = []
    for row in cat.INVOICE_TYPES:
        r = dict(row)
        ledger_code = r.pop("default_ledger_code", None)
        r["default_ledger_id"] = ledger_ids.get(ledger_code) if ledger_code else None
        inv_type_rows.append(r)
    await _upsert(session, InvoiceType, inv_type_rows)
    await _upsert(session, InvoiceStatus, cat.INVOICE_STATUSES)
    await _upsert(session, EmailDeliveryStatus, cat.EMAIL_DELIVERY_STATUSES)
    await _upsert(session, ProjectType, cat.PROJECT_TYPES)
    await _upsert(session, ProjectStatus, cat.PROJECT_STATUSES)
    await _upsert(session, PipelineStage, cat.PIPELINE_STAGES)
    await _upsert(session, EngagementKind, cat.ENGAGEMENT_KINDS)
    await _upsert(session, EngagementStatus, cat.ENGAGEMENT_STATUSES)
    await _upsert(session, EnquirySource, cat.ENQUIRY_SOURCES)
    await _upsert(session, LostReason, cat.LOST_REASONS)
    await _upsert(session, LeadStatus, cat.LEAD_STATUSES)
    await _upsert(session, LeadSource, cat.LEAD_SOURCES)
    await _upsert(session, DocumentKind, cat.DOCUMENT_KINDS)
    await _upsert(session, FeatureStatus, cat.FEATURE_STATUSES)
    await _upsert(session, LeaveType, cat.LEAVE_TYPES)
    await _upsert(session, ApprovalStatus, cat.APPROVAL_STATUSES)
    await _upsert(session, WorkTaskStatus, cat.WORK_TASK_STATUSES)

    section_ids = await _upsert(session, ExpenseSection, cat.EXPENSE_SECTIONS)
    cat_rows = []
    for row in cat.EXPENSE_CATEGORIES:
        r = dict(row)
        sec = r.pop("section_code", None)
        r["section_id"] = section_ids.get(sec) if sec else None
        cat_rows.append(r)
    await _upsert(session, ExpenseCategory, cat_rows)
    await _upsert(session, ExpenseClaimCategory, cat.EXPENSE_CLAIM_CATEGORIES)
    await _upsert(session, LateFeeType, cat.LATE_FEE_TYPES)
    await _upsert(session, LateFeeStatus, cat.LATE_FEE_STATUSES)
    await _upsert(session, AttendanceCorrectionReason, cat.ATTENDANCE_CORRECTION_REASONS)

    # Roles with party_type_id
    existing_roles = {
        r.code: r for r in (await session.execute(select(Role))).scalars().all()
    }
    for row in ROLE_SEED:
        pty = row["party_type"]
        data = {
            "id": row["id"],
            "code": row["code"],
            "name": row["name"],
            "description": row["description"],
            "party_type": pty,
            "party_type_id": party_ids[pty],
            "is_system": row["is_system"],
            "sort_order": row["sort_order"],
        }
        if row["code"] in existing_roles:
            role = existing_roles[row["code"]]
            for k, v in data.items():
                if k != "id":
                    setattr(role, k, v)
        else:
            session.add(Role(**data))
    await session.flush()
