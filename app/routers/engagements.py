from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..common.workflow import (
    DELIVERY_STAGES,
    MAX_STAGE,
    MIN_STAGE,
    PIPELINE_STAGES,
    SALES_MAX_STAGE,
    STAGES_BY_NUMBER,
    WARRANTY_DEFAULT_DAYS,
    build_checklist_seed,
)
from ..services.project_delivery import link_engagement_handoff
from ..services.engagement_files import (
    DOC_KINDS,
    delete_stored,
    read_stored,
    save_upload,
)
from ..core.dependencies import get_current_user, get_db, require_founder_ceo, is_privileged
from ..core.database import not_deleted
from ..models import (
    Client,
    Engagement,
    EngagementChecklistItem,
    EngagementDocument,
    Invoice,
    InvoiceItem,
    Project,
    ProjectWorker,
    RecurringInvoice,
    Settings,
    User,
)
from ..services.maintenance_billing import allocate_invoice_number, first_of_next_month
from ..schemas import (
    EngagementAdvanceInvoiceResponse,
    EngagementChecklistItemResponse,
    EngagementCreate,
    EngagementDetailResponse,
    EngagementDocumentResponse,
    EngagementEnsureProjectRequest,
    EngagementJumpRequest,
    EngagementLinkClientRequest,
    EngagementLinkInvoiceRequest,
    EngagementListItem,
    EngagementMaintenanceEnableRequest,
    EngagementMaintenanceUpdateRequest,
    EngagementMarkLostRequest,
    EngagementUpdate,
    InvoiceResponse,
    PipelineStageCount,
    PipelineSummaryResponse,
    RecurringInvoiceResponse,
)

router = APIRouter(prefix="/engagements", tags=["Engagements / Pipeline"])


def _stage_meta(stage: int):
    return STAGES_BY_NUMBER.get(stage) or STAGES_BY_NUMBER[1]


def _pipeline_value(eng: Engagement) -> Optional[int]:
    if eng.quoted_paise is not None:
        return eng.quoted_paise
    return eng.estimated_budget_paise


def _checklist_percent(items: List[EngagementChecklistItem], stage: Optional[int] = None) -> int:
    scoped = [i for i in items if stage is None or i.stage_number == stage]
    if not scoped:
        return 0
    done = sum(1 for i in scoped if i.is_done)
    return int(round(100 * done / len(scoped)))


def _seed_checklist(engagement_id: str) -> List[EngagementChecklistItem]:
    rows = []
    for stage_number, item_key, label, sort_order in build_checklist_seed():
        rows.append(
            EngagementChecklistItem(
                engagement_id=engagement_id,
                stage_number=stage_number,
                item_key=item_key,
                label=label,
                sort_order=sort_order,
                is_done=False,
            )
        )
    return rows


async def _worker_assigned(db: AsyncSession, eng: Engagement, user: User) -> bool:
    if not eng.project_id:
        return False
    stmt = select(ProjectWorker).where(
        ProjectWorker.project_id == eng.project_id,
        ProjectWorker.user_id == user.id,
    )
    return (await db.execute(stmt)).scalars().first() is not None


async def _load_engagement(db: AsyncSession, engagement_id: str) -> Engagement:
    stmt = (
        select(Engagement)
        .options(
            selectinload(Engagement.checklist_items),
            selectinload(Engagement.documents),
            selectinload(Engagement.client),
            selectinload(Engagement.project),
            selectinload(Engagement.owner),
            selectinload(Engagement.recurring_invoice).selectinload(RecurringInvoice.client),
        )
        .where(Engagement.id == engagement_id, not_deleted(Engagement))
    )
    eng = (await db.execute(stmt)).scalars().first()
    if not eng:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Engagement not found")
    return eng


async def _assert_can_view(db: AsyncSession, eng: Engagement, user: User) -> None:
    if is_privileged(user.role):
        return
    if user.role == "worker":
        if eng.current_stage < 5 or not await _worker_assigned(db, eng, user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return
    if user.role == "client":
        if not user.client_id or eng.client_id != user.client_id or eng.current_stage < 5:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


def _to_list_item(eng: Engagement, role: str) -> EngagementListItem:
    meta = _stage_meta(eng.current_stage)
    amc_active = bool(eng.recurring_invoice and eng.recurring_invoice.active)
    progress = _checklist_percent(list(eng.checklist_items or []), eng.current_stage)

    item = EngagementListItem(
        id=eng.id,
        title=eng.title,
        company_name=eng.company_name,
        contact_name=eng.contact_name,
        enquiry_source=eng.enquiry_source,
        engagement_kind=eng.engagement_kind,
        current_stage=eng.current_stage,
        stage_name=meta["name"],
        client_stage_label=meta["client_label"],
        status=eng.status,
        estimated_budget_paise=eng.estimated_budget_paise,
        quoted_paise=eng.quoted_paise,
        advance_paise=eng.advance_paise,
        pipeline_value_paise=_pipeline_value(eng),
        client_id=eng.client_id,
        client_name=eng.client.name if eng.client else None,
        project_id=eng.project_id,
        project_name=eng.project.name if eng.project else None,
        warranty_ends_on=eng.warranty_ends_on,
        amc_active=amc_active,
        checklist_progress_percent=progress,
        document_count=len(eng.documents or []),
        created_at=eng.created_at,
        updated_at=eng.updated_at,
    )

    if role == "worker":
        item.estimated_budget_paise = None
        item.quoted_paise = None
        item.advance_paise = None
        item.pipeline_value_paise = None
    elif role == "client":
        item.estimated_budget_paise = None
        item.advance_paise = None
        item.pipeline_value_paise = eng.quoted_paise
        item.stage_name = meta["client_label"]

    return item


async def _to_detail(db: AsyncSession, eng: Engagement, role: str) -> EngagementDetailResponse:
    base = _to_list_item(eng, role)
    meta = _stage_meta(eng.current_stage)

    checklist = [
        EngagementChecklistItemResponse.model_validate(i)
        for i in (eng.checklist_items or [])
    ]
    if role == "client":
        checklist = []

    recurring = None
    recent_invoices: List[InvoiceResponse] = []

    if is_privileged(role) and eng.recurring_invoice:
        recurring = RecurringInvoiceResponse.model_validate(eng.recurring_invoice)
        recurring.client_name = eng.recurring_invoice.client.name if eng.recurring_invoice.client else None

    if role in ("admin", "founder_ceo", "client") and eng.client_id:
        inv_stmt = (
            select(Invoice)
            .options(selectinload(Invoice.items))
            .where(Invoice.client_id == eng.client_id, Invoice.invoice_type == "maintenance")
            .order_by(Invoice.created_at.desc())
            .limit(5)
        )
        if role == "client":
            inv_stmt = inv_stmt.where(Invoice.status.in_(["sent", "paid", "overdue"]))
        invoices = (await db.execute(inv_stmt)).scalars().all()
        recent_invoices = [InvoiceResponse.model_validate(i) for i in invoices]

    documents = [EngagementDocumentResponse.model_validate(d) for d in (eng.documents or [])]

    detail = EngagementDetailResponse(
        **base.model_dump(),
        email=eng.email,
        phone=eng.phone,
        designation=eng.designation if role != "worker" else None,
        contact_email=eng.contact_email if role != "worker" else None,
        contact_phone=eng.contact_phone if role != "worker" else None,
        website=eng.website if role != "worker" else None,
        address=eng.address if role != "worker" else None,
        city=eng.city if role != "worker" else None,
        state=eng.state if role != "worker" else None,
        pincode=eng.pincode if role != "worker" else None,
        gstin=eng.gstin if role != "worker" else None,
        notes=eng.notes if role != "worker" else None,
        lost_reason=eng.lost_reason if is_privileged(role) else None,
        lost_note=eng.lost_note if is_privileged(role) else None,
        advance_invoice_id=eng.advance_invoice_id if is_privileged(role) else None,
        final_invoice_id=eng.final_invoice_id if is_privileged(role) else None,
        recurring_invoice_id=eng.recurring_invoice_id if is_privileged(role) else None,
        owner_user_id=eng.owner_user_id if is_privileged(role) else None,
        owner_name=eng.owner.full_name if eng.owner and is_privileged(role) else None,
        client_message=meta["client_message"],
        checklist_items=checklist,
        documents=documents,
        recurring=recurring if is_privileged(role) else None,
        recent_maintenance_invoices=recent_invoices if role != "worker" else [],
    )
    return detail


async def _ensure_project_internal(
    db: AsyncSession,
    eng: Engagement,
    project_name: Optional[str] = None,
    worker_ids: Optional[List[str]] = None,
    budget_paise: Optional[int] = None,
    development_days: Optional[int] = None,
    advance_paise: Optional[int] = None,
    maintenance_amount_paise: Optional[int] = None,
    maintenance_send_day: int = 1,
    maintenance_due_after_days: int = 4,
) -> Project:
    if not eng.client_id:
        client = Client(
            name=eng.company_name,
            primary_contact_name=eng.contact_name or None,
            designation=eng.designation,
            contact_email=eng.contact_email,
            contact_phone=eng.contact_phone,
            email=eng.email or eng.contact_email,
            phone=eng.phone,
            website=eng.website,
            address=eng.address,
            city=eng.city,
            state=eng.state,
            pincode=eng.pincode,
            gstin=eng.gstin,
            notes=f"Created from pipeline {eng.title}",
        )
        db.add(client)
        await db.flush()
        eng.client_id = client.id

    # Stage 05 confirmed terms are the source of truth (not pipeline quoted/advance)
    creating_new = not eng.project_id
    if creating_new:
        if budget_paise is None or int(budget_paise) <= 0:
            raise HTTPException(
                status_code=400,
                detail="Confirm total development amount on Stage 05 before converting",
            )
        if development_days is None or int(development_days) <= 0:
            raise HTTPException(
                status_code=400,
                detail="Confirm development days on Stage 05 before converting",
            )
        if advance_paise is None:
            raise HTTPException(
                status_code=400,
                detail="Confirm advance amount on Stage 05 (use 0 if none)",
            )

    resolved_budget = int(budget_paise) if budget_paise is not None else 0
    resolved_advance = int(advance_paise) if advance_paise is not None else 0
    resolved_days = development_days

    if eng.project_id:
        project = (
            await db.execute(
                select(Project)
                .options(selectinload(Project.workers), selectinload(Project.recurring_invoices))
                .where(Project.id == eng.project_id, not_deleted(Project))
            )
        ).scalars().first()
        if not project:
            raise HTTPException(status_code=404, detail="Linked project not found")
        if budget_paise is not None:
            project.budget_paise = resolved_budget
        if development_days is not None:
            project.development_days = resolved_days
        if project_name and project_name.strip():
            project.name = project_name.strip()
        if eng.notes:
            project.description = eng.notes
    else:
        from ..services.project_clients import set_project_clients

        project = Project(
            name=(project_name or eng.title).strip(),
            client_id=eng.client_id,
            description=eng.notes or f"Created from engagement: {eng.title}",
            budget_paise=resolved_budget,
            development_days=resolved_days,
            status="lead",
            delivery_phase=5,
        )
        db.add(project)
        await db.flush()
        await set_project_clients(db, project, [eng.client_id], primary_client_id=eng.client_id)
        eng.project_id = project.id

    if worker_ids:
        existing = {
            r.user_id
            for r in (
                await db.execute(select(ProjectWorker).where(ProjectWorker.project_id == project.id))
            ).scalars().all()
        }
        for wid in worker_ids:
            if wid not in existing:
                db.add(ProjectWorker(project_id=project.id, user_id=wid))

    # Mark onboarding checklist keys if present
    for item in eng.checklist_items or []:
        if item.stage_number == 5 and item.item_key == "create_project":
            item.is_done = True
            item.done_at = datetime.now(timezone.utc)
        if item.stage_number == 5 and item.item_key == "assign_team" and worker_ids:
            item.is_done = True
            item.done_at = datetime.now(timezone.utc)

    # Attach AMC schedule configured earlier, or create from conversion payload
    if eng.recurring_invoice_id:
        rec = eng.recurring_invoice
        if rec and not rec.project_id:
            rec.project_id = project.id
        if rec and maintenance_amount_paise is not None and maintenance_amount_paise > 0:
            rec.amount_paise = maintenance_amount_paise
            rec.send_day = maintenance_send_day
            rec.due_after_days = maintenance_due_after_days
            rec.day_of_month = maintenance_send_day
    elif maintenance_amount_paise and maintenance_amount_paise > 0:
        from ..services.maintenance_billing import upsert_project_maintenance

        project = (
            await db.execute(
                select(Project)
                .options(selectinload(Project.recurring_invoices))
                .where(Project.id == project.id, not_deleted(Project))
            )
        ).scalars().first()
        rec = await upsert_project_maintenance(
            db,
            project,
            maintenance_amount_paise,
            send_day=maintenance_send_day,
            due_after_days=maintenance_due_after_days,
        )
        if rec:
            eng.recurring_invoice_id = rec.id

    # Handoff: seed project delivery checklist + bidirectional link
    await link_engagement_handoff(db, eng, project, enter_delivery=eng.current_stage >= 5)

    return project


# ---------- Catalog ----------

@router.get("/catalog/stages")
async def get_stage_catalog(current_user: User = Depends(get_current_user)):
    return {
        "stages": PIPELINE_STAGES,
        "role": current_user.role,
    }


@router.get("/pipeline", response_model=PipelineSummaryResponse)
async def pipeline_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(Engagement).options(selectinload(Engagement.recurring_invoice))

    if current_user.role == "worker":
        stmt = (
            stmt.join(Project, Engagement.project_id == Project.id)
            .join(ProjectWorker, ProjectWorker.project_id == Project.id)
            .where(ProjectWorker.user_id == current_user.id, Engagement.current_stage >= 5)
        )
    elif current_user.role == "client":
        if not current_user.client_id:
            return PipelineSummaryResponse(stages=[], open_value_paise=0, lost_this_month=0, maintenance_active=0)
        stmt = stmt.where(Engagement.client_id == current_user.client_id, Engagement.current_stage >= 5)

    engagements = (await db.execute(stmt)).scalars().unique().all()

    counts = {s["number"]: 0 for s in PIPELINE_STAGES}
    open_value = 0
    lost_month = 0
    maint = 0
    month_start = date.today().replace(day=1)

    for eng in engagements:
        if eng.status in ("active", "on_hold", "maintenance"):
            counts[eng.current_stage] = counts.get(eng.current_stage, 0) + 1
            val = _pipeline_value(eng)
            if val and is_privileged(current_user.role):
                open_value += val
        if eng.status == "lost" and eng.updated_at and eng.updated_at.date() >= month_start:
            lost_month += 1
        if eng.status == "maintenance" or (eng.recurring_invoice and eng.recurring_invoice.active):
            maint += 1

    stages = [
        PipelineStageCount(stage=s["number"], name=s["name"], count=counts.get(s["number"], 0))
        for s in PIPELINE_STAGES
    ]
    if not is_privileged(current_user.role):
        open_value = 0

    return PipelineSummaryResponse(
        stages=stages,
        open_value_paise=open_value,
        lost_this_month=lost_month if is_privileged(current_user.role) else 0,
        maintenance_active=maint,
    )


@router.get("", response_model=List[EngagementListItem])
async def list_engagements(
    stage: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    kind: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    include_lost: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = (
        select(Engagement)
        .options(
            selectinload(Engagement.checklist_items),
            selectinload(Engagement.documents),
            selectinload(Engagement.client),
            selectinload(Engagement.project),
            selectinload(Engagement.recurring_invoice),
        )
        .where(not_deleted(Engagement))
        .order_by(Engagement.updated_at.desc())
    )

    if current_user.role == "worker":
        stmt = (
            stmt.join(Project, Engagement.project_id == Project.id)
            .join(ProjectWorker, ProjectWorker.project_id == Project.id)
            .where(ProjectWorker.user_id == current_user.id, Engagement.current_stage >= 5)
        )
    elif current_user.role == "client":
        if not current_user.client_id:
            return []
        stmt = stmt.where(Engagement.client_id == current_user.client_id, Engagement.current_stage >= 5)

    if stage is not None:
        stmt = stmt.where(Engagement.current_stage == stage)
    if project_id:
        stmt = stmt.where(Engagement.project_id == project_id)
    if kind:
        stmt = stmt.where(Engagement.engagement_kind == kind)
    if status_filter and status_filter != "all":
        stmt = stmt.where(Engagement.status == status_filter)
    elif not include_lost and is_privileged(current_user.role):
        stmt = stmt.where(Engagement.status != "lost")
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            (Engagement.title.ilike(term))
            | (Engagement.company_name.ilike(term))
            | (Engagement.contact_name.ilike(term))
        )

    rows = (await db.execute(stmt)).scalars().unique().all()
    return [_to_list_item(e, current_user.role) for e in rows]


@router.post("", response_model=EngagementDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_engagement(
    payload: EngagementCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    kind = payload.engagement_kind if payload.engagement_kind in ("project", "amc_only") else "project"
    eng = Engagement(
        title=payload.title.strip(),
        company_name=payload.company_name.strip(),
        contact_name=(payload.contact_name or "").strip(),
        designation=(payload.designation or None),
        contact_email=(payload.contact_email or None),
        contact_phone=(payload.contact_phone or None),
        email=(payload.email or payload.contact_email or None),
        phone=(payload.phone or None),
        website=(payload.website or None),
        address=(payload.address or None),
        city=(payload.city or None),
        state=(payload.state or None),
        pincode=(payload.pincode or None),
        gstin=(payload.gstin or None),
        enquiry_source=payload.enquiry_source,
        engagement_kind=kind,
        current_stage=1,
        status="active",
        notes=payload.notes,
        estimated_budget_paise=payload.estimated_budget_paise,
        client_id=payload.client_id,
        owner_user_id=payload.owner_user_id or current_user.id,
    )
    db.add(eng)
    await db.flush()
    for item in _seed_checklist(eng.id):
        db.add(item)
    await db.commit()
    eng = await _load_engagement(db, eng.id)
    return await _to_detail(db, eng, current_user.role)


@router.get("/{engagement_id}", response_model=EngagementDetailResponse)
async def get_engagement(
    engagement_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    eng = await _load_engagement(db, engagement_id)
    await _assert_can_view(db, eng, current_user)
    return await _to_detail(db, eng, current_user.role)


@router.post(
    "/{engagement_id}/documents",
    response_model=EngagementDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_engagement_document(
    engagement_id: str,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    doc_kind: str = Form("enquiry"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    kind = doc_kind if doc_kind in DOC_KINDS else "enquiry"
    stored, original, content_type, size = await save_upload(eng.id, file)
    doc = EngagementDocument(
        engagement_id=eng.id,
        title=(title or original).strip() or original,
        original_filename=original,
        stored_filename=stored,
        content_type=content_type,
        size_bytes=size,
        doc_kind=kind,
        uploaded_by=current_user.id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return EngagementDocumentResponse.model_validate(doc)


@router.get("/{engagement_id}/documents/{doc_id}/file")
async def view_engagement_document(
    engagement_id: str,
    doc_id: str,
    download: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    eng = await _load_engagement(db, engagement_id)
    await _assert_can_view(db, eng, current_user)
    doc = next((d for d in (eng.documents or []) if d.id == doc_id), None)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    data = read_stored(eng.id, doc.stored_filename)
    filename = (doc.original_filename or "document").replace('"', "")
    disposition = "attachment" if download else "inline"
    return Response(
        content=data,
        media_type=doc.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.delete("/{engagement_id}/documents/{doc_id}", status_code=status.HTTP_200_OK)
async def delete_engagement_document(
    engagement_id: str,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    doc = next((d for d in (eng.documents or []) if d.id == doc_id), None)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    delete_stored(eng.id, doc.stored_filename)
    await db.delete(doc)
    await db.commit()
    return {"message": "Document deleted"}


@router.patch("/{engagement_id}", response_model=EngagementDetailResponse)
async def update_engagement(
    engagement_id: str,
    payload: EngagementUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(eng, k, v)
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/checklist/{item_id}/toggle", response_model=EngagementDetailResponse)
async def toggle_checklist_item(
    engagement_id: str,
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    eng = await _load_engagement(db, engagement_id)
    await _assert_can_view(db, eng, current_user)

    item = next((i for i in eng.checklist_items if i.id == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")

    if current_user.role == "client":
        raise HTTPException(status_code=403, detail="Clients cannot update checklist")
    # Delivery checklists (06+) are owned by the Project — Pipeline only edits sales 01–05
    if item.stage_number > SALES_MAX_STAGE:
        raise HTTPException(
            status_code=400,
            detail="Delivery checklist is managed on the Project. Open the linked project to update.",
        )
    if current_user.role == "worker":
        raise HTTPException(
            status_code=403,
            detail="Workers update delivery checklists on the Project page",
        )

    item.is_done = not item.is_done
    item.done_at = datetime.now(timezone.utc) if item.is_done else None
    item.done_by = current_user.id if item.is_done else None
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


def _can_advance_from(eng: Engagement) -> Optional[str]:
    stage = eng.current_stage
    if eng.status in ("lost", "completed"):
        return "Cannot advance a lost or completed engagement"
    if eng.status == "on_hold":
        return "Resume the engagement before advancing"
    if stage == 1 and not eng.enquiry_source:
        return "Set enquiry source before advancing from stage 01"
    if stage == 3 and eng.quoted_paise is None:
        return "Set quoted amount before advancing from quotation"
    if stage == 4 and not eng.client_id and eng.engagement_kind != "amc_only":
        return "Link a client before advancing from contract"
    if stage == 4 and eng.engagement_kind == "amc_only" and not eng.client_id:
        return "Link a client before advancing AMC-only engagement"
    if stage >= MAX_STAGE:
        return "Already at final stage — use maintenance close or complete"
    return None


@router.post("/{engagement_id}/advance", response_model=EngagementDetailResponse)
async def advance_engagement(
    engagement_id: str,
    close_without_amc: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    eng = await _load_engagement(db, engagement_id)
    await _assert_can_view(db, eng, current_user)

    if current_user.role == "client":
        raise HTTPException(status_code=403, detail="Forbidden")
    # Delivery (06+) is owned by Projects — Pipeline only advances sales through onboarding (≤05)
    if current_user.role == "worker":
        raise HTTPException(
            status_code=403,
            detail="Workers advance delivery on the Project page, not Pipeline",
        )
    if eng.current_stage >= SALES_MAX_STAGE and eng.project_id and eng.current_stage >= 5:
        if eng.current_stage > SALES_MAX_STAGE:
            raise HTTPException(
                status_code=400,
                detail="Delivery is managed on the Project. Open the linked project to advance.",
            )

    err = _can_advance_from(eng)
    if err:
        raise HTTPException(status_code=400, detail=err)

    # Require current-stage checklist complete for stages 2–5
    if 2 <= eng.current_stage <= SALES_MAX_STAGE:
        stage_items = [i for i in eng.checklist_items if i.stage_number == eng.current_stage]
        if stage_items and not all(i.is_done for i in stage_items):
            raise HTTPException(status_code=400, detail="Complete current stage checklist before advancing")

    # Leaving stage 4 into onboarding
    if eng.current_stage == 4:
        if eng.engagement_kind == "amc_only":
            eng.current_stage = 11
            eng.status = "maintenance"
            await db.commit()
            eng = await _load_engagement(db, engagement_id)
            return await _to_detail(db, eng, current_user.role)
        if not eng.client_id:
            raise HTTPException(status_code=400, detail="Link a client before advancing to onboarding")

    # Completing stage 5 → handoff into Project delivery (phase 6)
    if eng.current_stage == 5:
        if not eng.project_id:
            raise HTTPException(
                status_code=400,
                detail="Convert to project with confirmed commercial terms before completing onboarding",
            )
        project = await _ensure_project_internal(db, eng)
        await link_engagement_handoff(db, eng, project, enter_delivery=True)
        await db.commit()
        eng = await _load_engagement(db, engagement_id)
        return await _to_detail(db, eng, current_user.role)

    if eng.current_stage >= SALES_MAX_STAGE:
        raise HTTPException(
            status_code=400,
            detail="Use the Project Delivery tab to continue past onboarding",
        )

    eng.current_stage = min(eng.current_stage + 1, SALES_MAX_STAGE)
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/regress", response_model=EngagementDetailResponse)
async def regress_engagement(
    engagement_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    if eng.current_stage <= MIN_STAGE:
        raise HTTPException(status_code=400, detail="Already at first stage")
    if eng.status == "lost":
        raise HTTPException(status_code=400, detail="Cannot regress a lost engagement")
    eng.current_stage -= 1
    if eng.status == "maintenance" and eng.current_stage < 11:
        eng.status = "active"
    if eng.status == "completed":
        eng.status = "active"
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/jump", response_model=EngagementDetailResponse)
async def jump_engagement(
    engagement_id: str,
    payload: EngagementJumpRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    target = payload.target_stage
    if target < MIN_STAGE or target > MAX_STAGE:
        raise HTTPException(status_code=400, detail="Invalid target stage")

    if eng.engagement_kind == "amc_only":
        if target not in (4, 11):
            raise HTTPException(status_code=400, detail="AMC-only engagements may jump to stage 04 or 11 only")
        if target == 11 and not eng.client_id:
            raise HTTPException(status_code=400, detail="Link a client before jumping to maintenance")
    else:
        # Allow founder jump freely within active deals (ops flexibility)
        pass

    eng.current_stage = target
    if target == 11:
        eng.status = "maintenance"
    elif eng.status == "maintenance":
        eng.status = "active"
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/mark-lost", response_model=EngagementDetailResponse)
async def mark_lost(
    engagement_id: str,
    payload: EngagementMarkLostRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    allowed = {"price", "timing", "competitor", "no_response", "other"}
    if payload.lost_reason not in allowed:
        raise HTTPException(status_code=400, detail="Invalid lost_reason")
    eng.status = "lost"
    eng.lost_reason = payload.lost_reason
    eng.lost_note = payload.lost_note
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/hold", response_model=EngagementDetailResponse)
async def hold_engagement(
    engagement_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    if eng.status in ("lost", "completed"):
        raise HTTPException(status_code=400, detail="Cannot hold this engagement")
    eng.status = "on_hold"
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/resume", response_model=EngagementDetailResponse)
async def resume_engagement(
    engagement_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    if eng.status != "on_hold":
        raise HTTPException(status_code=400, detail="Engagement is not on hold")
    eng.status = "maintenance" if eng.current_stage == 11 else "active"
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/link-client", response_model=EngagementDetailResponse)
async def link_client(
    engagement_id: str,
    payload: EngagementLinkClientRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    if payload.client_id:
        client = (
            await db.execute(select(Client).where(Client.id == payload.client_id, not_deleted(Client)))
        ).scalars().first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        eng.client_id = client.id
    elif payload.create_client:
        client = Client(
            name=eng.company_name,
            primary_contact_name=eng.contact_name or None,
            designation=eng.designation,
            contact_email=eng.contact_email,
            contact_phone=eng.contact_phone,
            email=eng.email or eng.contact_email,
            phone=eng.phone,
            website=eng.website,
            address=eng.address,
            city=eng.city,
            state=eng.state,
            pincode=eng.pincode,
            gstin=eng.gstin,
            notes=f"Created from pipeline {eng.title}",
        )
        db.add(client)
        await db.flush()
        eng.client_id = client.id
    else:
        raise HTTPException(status_code=400, detail="Provide client_id or create_client=true")
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/ensure-project", response_model=EngagementDetailResponse)
async def ensure_project(
    engagement_id: str,
    payload: EngagementEnsureProjectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    if eng.current_stage < 5:
        eng.current_stage = 5
    project = await _ensure_project_internal(
        db,
        eng,
        project_name=payload.project_name,
        worker_ids=payload.worker_ids,
        budget_paise=payload.budget_paise,
        development_days=payload.development_days,
        advance_paise=payload.advance_paise,
        maintenance_amount_paise=payload.maintenance_amount_paise,
        maintenance_send_day=payload.maintenance_send_day,
        maintenance_due_after_days=payload.maintenance_due_after_days,
    )
    await link_engagement_handoff(db, eng, project, enter_delivery=True)
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/link-invoice", response_model=EngagementDetailResponse)
async def link_invoice(
    engagement_id: str,
    payload: EngagementLinkInvoiceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    inv = (
        await db.execute(select(Invoice).where(Invoice.id == payload.invoice_id, not_deleted(Invoice)))
    ).scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if payload.role == "advance":
        eng.advance_invoice_id = inv.id
        if eng.advance_paise is None:
            eng.advance_paise = inv.total_paise
    elif payload.role == "final":
        eng.final_invoice_id = inv.id
    else:
        raise HTTPException(status_code=400, detail="role must be 'advance' or 'final'")
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/advance-invoice", response_model=EngagementAdvanceInvoiceResponse)
async def create_advance_invoice(
    engagement_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    """Create a draft project invoice from advance_paise and link it as advance_invoice_id."""
    eng = await _load_engagement(db, engagement_id)
    if not eng.client_id:
        raise HTTPException(status_code=400, detail="Link a client before creating an advance invoice")
    if not eng.advance_paise or eng.advance_paise <= 0:
        raise HTTPException(status_code=400, detail="Set advance amount before creating the invoice")

    # Reuse existing linked advance invoice if still draft
    if eng.advance_invoice_id:
        existing = (
            await db.execute(select(Invoice).where(Invoice.id == eng.advance_invoice_id))
        ).scalars().first()
        if existing:
            detail = await _to_detail(db, eng, current_user.role)
            return EngagementAdvanceInvoiceResponse(
                invoice_id=existing.id,
                invoice_number=existing.invoice_number,
                total_paise=existing.total_paise,
                status=existing.status,
                engagement=detail,
            )

    st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalar_one_or_none()
    from ..services.tax_payroll import effective_tax_rate, compute_tax_paise
    tax_rate = effective_tax_rate(st)
    # advance_paise is treated as base (ex-GST) to match money panel semantics
    subtotal = int(eng.advance_paise)
    tax_paise = compute_tax_paise(subtotal, tax_rate)
    total_paise = subtotal + tax_paise
    today = date.today()
    inv_number = await allocate_invoice_number(
        db, invoice_type="project", issue_date=today
    )

    inv = Invoice(
        invoice_number=inv_number,
        invoice_type="project",
        client_id=eng.client_id,
        project_id=eng.project_id,
        status="draft",
        issue_date=today,
        due_date=today + timedelta(days=7),
        subtotal_paise=subtotal,
        tax_rate_percent=tax_rate,
        tax_paise=tax_paise,
        total_paise=total_paise,
        email_status="pending",
        notes=f"Advance payment · {eng.title}",
    )
    db.add(inv)
    await db.flush()
    db.add(
        InvoiceItem(
            invoice_id=inv.id,
            description=f"Project advance — {eng.title}",
            quantity=1.0,
            unit_rate_paise=subtotal,
            amount_paise=subtotal,
        )
    )
    eng.advance_invoice_id = inv.id

    # Mark checklist "raise invoice" if present
    for item in eng.checklist_items or []:
        if item.stage_number == 4 and item.item_key in ("raise_invoice", "raise_advance_invoice"):
            item.is_done = True
            item.done_at = datetime.now(timezone.utc)
            item.done_by = current_user.id

    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    detail = await _to_detail(db, eng, current_user.role)
    return EngagementAdvanceInvoiceResponse(
        invoice_id=inv.id,
        invoice_number=inv.invoice_number,
        total_paise=total_paise,
        status="draft",
        engagement=detail,
    )


# ---------- Maintenance bridge ----------

@router.post("/{engagement_id}/maintenance/enable", response_model=EngagementDetailResponse)
async def enable_maintenance(
    engagement_id: str,
    payload: EngagementMaintenanceEnableRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    if not eng.client_id:
        raise HTTPException(status_code=400, detail="Link a client before enabling AMC")

    today = date.today()
    # Schedule first full month for next send_day (pro-rata runs on project complete)
    next_run = today.replace(day=min(payload.send_day, 28))
    if next_run <= today:
        next_run = first_of_next_month(today).replace(day=min(payload.send_day, 28))

    if eng.recurring_invoice_id:
        rec = eng.recurring_invoice
        if not rec:
            raise HTTPException(status_code=404, detail="Linked recurring invoice missing")
        rec.amount_paise = payload.amount_paise
        rec.send_day = payload.send_day
        rec.day_of_month = payload.send_day
        rec.due_after_days = payload.due_after_days
        rec.reminder_every_days = payload.reminder_every_days
        rec.tax_rate_percent = payload.tax_rate_percent
        rec.line_items_template = [
            {"description": payload.description, "quantity": 1, "unit_rate_paise": payload.amount_paise}
        ]
        rec.active = True
        rec.next_run_date = next_run
        if eng.project_id and not rec.project_id:
            rec.project_id = eng.project_id
    else:
        rec = RecurringInvoice(
            client_id=eng.client_id,
            project_id=eng.project_id,
            amount_paise=payload.amount_paise,
            send_day=payload.send_day,
            due_after_days=payload.due_after_days,
            reminder_every_days=payload.reminder_every_days,
            starts_on=today,
            line_items_template=[
                {"description": payload.description, "quantity": 1, "unit_rate_paise": payload.amount_paise}
            ],
            tax_rate_percent=payload.tax_rate_percent,
            day_of_month=payload.send_day,
            next_run_date=next_run,
            active=True,
            notes=f"AMC from engagement {eng.id}",
        )
        db.add(rec)
        await db.flush()
        eng.recurring_invoice_id = rec.id

    if payload.jump_to_amc_stage:
        eng.current_stage = 11
        eng.status = "maintenance"
        if not eng.warranty_ends_on:
            eng.warranty_ends_on = today + timedelta(days=WARRANTY_DEFAULT_DAYS)

        for item in eng.checklist_items or []:
            if item.stage_number == 11 and item.item_key in ("enable_retainer", "set_amount_schedule"):
                item.is_done = True
                item.done_at = datetime.now(timezone.utc)
                item.done_by = current_user.id

    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/maintenance/pause", response_model=EngagementDetailResponse)
async def pause_maintenance(
    engagement_id: str,
    complete: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    if not eng.recurring_invoice:
        raise HTTPException(status_code=400, detail="No AMC schedule linked")
    eng.recurring_invoice.active = False
    if complete:
        eng.status = "completed"
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.post("/{engagement_id}/maintenance/resume", response_model=EngagementDetailResponse)
async def resume_maintenance(
    engagement_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    if not eng.recurring_invoice:
        raise HTTPException(status_code=400, detail="No AMC schedule linked")
    eng.recurring_invoice.active = True
    eng.current_stage = 11
    eng.status = "maintenance"
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)


@router.patch("/{engagement_id}/maintenance", response_model=EngagementDetailResponse)
async def update_maintenance(
    engagement_id: str,
    payload: EngagementMaintenanceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    eng = await _load_engagement(db, engagement_id)
    if not eng.recurring_invoice:
        raise HTTPException(status_code=400, detail="No AMC schedule linked")
    rec = eng.recurring_invoice
    data = payload.model_dump(exclude_unset=True)
    if "send_day" in data and data["send_day"] is not None:
        rec.send_day = data["send_day"]
        rec.day_of_month = data["send_day"]
    if "amount_paise" in data and data["amount_paise"] is not None:
        rec.amount_paise = data["amount_paise"]
        if rec.line_items_template:
            rec.line_items_template = [
                {**rec.line_items_template[0], "unit_rate_paise": data["amount_paise"]}
            ]
    for key in ("due_after_days", "reminder_every_days", "active", "notes"):
        if key in data and data[key] is not None:
            setattr(rec, key, data[key])
    await db.commit()
    eng = await _load_engagement(db, engagement_id)
    return await _to_detail(db, eng, current_user.role)
