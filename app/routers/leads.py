from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from ..core.dependencies import get_db, require_founder_ceo
from ..core.database import not_deleted
from ..models import Lead, Client, Project, User
from ..schemas import (
    LeadResponse, LeadCreate, LeadUpdate,
    LeadConvertRequest, LeadConvertResponse,
    LeadListResponse,
    OptionsResponse,
    OptionItemSchema,
)
from ..services.audit import audit_create, audit_soft_delete, audit_update, snapshot_entity
from ..services.pagination import (
    build_envelope,
    clamp_options_limit,
    clamp_page,
    clamp_page_size,
    offset_for,
)

router = APIRouter(prefix="/leads", tags=["Leads (Founder Only)"])

@router.get("", response_model=LeadListResponse)
async def list_leads(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    sort_by: Optional[str] = Query("created_at"),
    sort_order: Optional[str] = Query("desc"),
    include_counts: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    page = clamp_page(page)
    page_size = clamp_page_size(page_size)
    stmt = select(Lead).where(not_deleted(Lead))
    if status and status != "all":
        stmt = stmt.where(Lead.status == status)
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Lead.name.ilike(term),
                Lead.company.ilike(term),
                Lead.contact_name.ilike(term),
                Lead.email.ilike(term),
            )
        )

    sort_map = {
        "created_at": Lead.created_at,
        "name": Lead.name,
        "company": Lead.company,
        "status": Lead.status,
    }
    sort_col = sort_map.get((sort_by or "created_at").strip(), Lead.created_at)
    order = sort_col.asc() if (sort_order or "desc").lower() == "asc" else sort_col.desc()

    total_items = int(
        await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
        or 0
    )
    res = await db.execute(
        stmt.order_by(order).offset(offset_for(page, page_size)).limit(page_size)
    )
    items = [LeadResponse.model_validate(l) for l in res.scalars().all()]
    out = LeadListResponse(
        **build_envelope(items, page=page, page_size=page_size, total_items=total_items)
    )

    if include_counts:
        counts_stmt = select(Lead.status, func.count()).where(not_deleted(Lead))
        if search and search.strip():
            term = f"%{search.strip()}%"
            counts_stmt = counts_stmt.where(
                or_(
                    Lead.name.ilike(term),
                    Lead.company.ilike(term),
                    Lead.contact_name.ilike(term),
                    Lead.email.ilike(term),
                )
            )
        counts_stmt = counts_stmt.group_by(Lead.status)
        rows = (await db.execute(counts_stmt)).all()
        counts: dict[str, int] = {
            "all": 0,
            "new": 0,
            "contacted": 0,
            "proposal": 0,
            "won": 0,
            "lost": 0,
        }
        for st_raw, n in rows:
            n_i = int(n or 0)
            counts["all"] += n_i
            key = str(st_raw or "")
            counts[key] = counts.get(key, 0) + n_i
        out.status_counts = counts

    return out


@router.get("/options", response_model=OptionsResponse)
async def list_lead_options(
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    lim = clamp_options_limit(limit)
    stmt = select(Lead).where(not_deleted(Lead)).order_by(Lead.created_at.desc()).limit(lim)
    if search and search.strip():
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(Lead.name.ilike(term), Lead.company.ilike(term), Lead.contact_name.ilike(term))
        )
    rows = (await db.execute(stmt)).scalars().all()
    return OptionsResponse(
        items=[
            OptionItemSchema(
                id=l.id,
                label=f"{l.name} — {l.company}",
                meta={"status": l.status},
            )
            for l in rows
        ]
    )

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
        from ..services.project_clients import set_project_clients

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
        await set_project_clients(db, project, [client.id], primary_client_id=client.id)
        project_id = project.id

    # 2. Update lead status to won
    lead.status = "won"
    await db.commit()

    return LeadConvertResponse(
        client_id=client.id,
        project_id=project_id,
        message=f"Successfully converted lead '{lead.name}' to Client and Project."
    )

