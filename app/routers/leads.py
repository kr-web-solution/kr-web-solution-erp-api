from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..core.dependencies import get_db, require_founder_ceo
from ..core.database import not_deleted
from ..models import Lead, Client, Project, User
from ..schemas import (
    LeadResponse, LeadCreate, LeadUpdate,
    LeadConvertRequest, LeadConvertResponse
)
from ..services.audit import audit_create, audit_soft_delete, audit_update, snapshot_entity

router = APIRouter(prefix="/leads", tags=["Leads (Founder Only)"])

@router.get("", response_model=List[LeadResponse])
async def list_leads(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    stmt = select(Lead).where(not_deleted(Lead)).order_by(Lead.created_at.desc())
    if status and status != "all":
        stmt = stmt.where(Lead.status == status)
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where((Lead.name.ilike(term)) | (Lead.company.ilike(term)) | (Lead.contact_name.ilike(term)))

    res = await db.execute(stmt)
    return [LeadResponse.model_validate(l) for l in res.scalars().all()]

@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Lead).where(Lead.id == lead_id, not_deleted(Lead)))
    lead = res.scalars().first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return LeadResponse.model_validate(lead)

@router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(
    payload: LeadCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    lead = Lead(**payload.model_dump())
    db.add(lead)
    await db.flush()
    await audit_create(db, lead, actor_id=current_user.id, request=request)
    await db.commit()
    await db.refresh(lead)
    return LeadResponse.model_validate(lead)

@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: str,
    payload: LeadUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Lead).where(Lead.id == lead_id, not_deleted(Lead)))
    lead = res.scalars().first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    before = snapshot_entity(lead)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(lead, k, v)

    await audit_update(db, lead, before, actor_id=current_user.id, request=request)
    await db.commit()
    await db.refresh(lead)
    return LeadResponse.model_validate(lead)

@router.delete("/{lead_id}", status_code=status.HTTP_200_OK)
async def delete_lead(
    lead_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Lead).where(Lead.id == lead_id, not_deleted(Lead)))
    lead = res.scalars().first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    lead.soft_delete()
    await audit_soft_delete(db, lead, actor_id=current_user.id, request=request)
    await db.commit()
    return {"message": "Lead deleted successfully"}

@router.post("/{lead_id}/convert", response_model=LeadConvertResponse)
async def convert_lead(
    lead_id: str,
    payload: LeadConvertRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    """Transactionally convert a won lead into a Client and optional Project."""
    res = await db.execute(select(Lead).where(Lead.id == lead_id, not_deleted(Lead)))
    lead = res.scalars().first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    # 1. Create client
    client = Client(
        name=lead.company or lead.name,
        primary_contact_name=lead.contact_name or "Primary Contact",
        email=lead.email or f"info@{lead.company.lower().replace(' ', '')}.com",
        phone=lead.phone,
        notes=f"Converted from lead '{lead.name}'. {lead.notes or ''}".strip()
    )
    db.add(client)
    await db.flush()

    project_id: Optional[str] = None
    if payload.create_project:
        project_name = payload.project_name or f"{client.name} Implementation"
        project = Project(
            name=project_name,
            client_id=client.id,
            description=f"Initial project created upon converting lead: {lead.name}",
            budget_paise=payload.budget_paise or 0,
            status="running"
        )
        db.add(project)
        await db.flush()
        project_id = project.id

    # 2. Update lead status to won
    lead.status = "won"
    await db.commit()

    return LeadConvertResponse(
        client_id=client.id,
        project_id=project_id,
        message=f"Successfully converted lead '{lead.name}' to Client and Project."
    )

