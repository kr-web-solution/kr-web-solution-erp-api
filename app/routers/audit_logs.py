"""Founder/admin-only audit log API."""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.dependencies import get_db, require_founder_ceo
from ..models import AuditLog, User

router = APIRouter(prefix="/audit-logs", tags=["Audit (Founder/Admin)"])


class AuditLogResponse(BaseModel):
    id: str
    actor_id: Optional[str] = None
    actor_email: Optional[str] = None
    actor_name: Optional[str] = None
    entity_type: str
    entity_id: str
    action: str
    changed_fields: list
    before: Optional[dict] = None
    after: Optional[dict] = None
    ip: Optional[str] = None
    created_at: str

    model_config = {"from_attributes": True}


@router.get("", response_model=List[AuditLogResponse])
async def list_audit_logs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    stmt = (
        select(AuditLog)
        .options(selectinload(AuditLog.actor))
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)

    rows = (await db.execute(stmt)).scalars().all()
    out: list[AuditLogResponse] = []
    for r in rows:
        out.append(
            AuditLogResponse(
                id=r.id,
                actor_id=r.actor_id,
                actor_email=r.actor.email if r.actor else None,
                actor_name=r.actor.full_name if r.actor else None,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                action=r.action,
                changed_fields=r.changed_fields or [],
                before=r.before,
                after=r.after,
                ip=r.ip,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
        )
    return out
