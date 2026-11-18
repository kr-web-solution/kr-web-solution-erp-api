from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func  # noqa: F401 — delete used in update_project
from sqlalchemy.orm import selectinload

from ..core.dependencies import get_db, get_current_user, require_founder_ceo, is_privileged
from ..common.workflow import DELIVERY_MAX_STAGE, DELIVERY_MIN_STAGE, WARRANTY_DEFAULT_DAYS
from ..models import (
    Project,
    Client,
    User,
    ProjectWorker,
    RecurringInvoice,
    ProjectChecklistItem,
    ProjectFeature,
    ProjectFeatureDoc,
    ProjectDocument,
    Engagement,
)
from ..schemas import (
    ProjectResponse,
    ProjectCreate,
    ProjectUpdate,
    ProjectProrataResponse,
    ProjectGoLiveRequest,
    ProjectInvoiceCreateResponse,
    UserResponse,
    ProjectChecklistItemResponse,
    ProjectDeliveryDetailResponse,
    ProjectFeatureCreate,
    ProjectFeatureUpdate,
    ProjectFeatureResponse,
    ProjectFeatureDocCreate,
    ProjectFeatureDocResponse,
    ProjectDocumentResponse,
)
from ..models import Invoice
from ..services.project_delivery import (
    checklist_progress,
    ensure_project_checklist,
    phase_name,
    sync_engagement_from_project,
)
from ..services.project_files import (
    DOC_KINDS,
    delete_stored,
    read_stored,
    save_project_upload,
)
from ..services.maintenance_billing import (
    create_prorata_invoice,
    create_project_amount_invoice,
    upsert_project_maintenance,
)

router = APIRouter(prefix="/projects", tags=["Projects"])

FEATURE_STATUSES = {"requested", "approved", "in_progress", "done", "rejected"}
CLIENT_VISIBLE_FEATURE_STATUSES = {"approved", "in_progress", "done"}


def sanitize_project_for_role(
    project: Project,
    role: str,
    *,
    invoiced_paise: Optional[int] = None,
) -> ProjectResponse:
    resp = ProjectResponse.model_validate(project)
    resp.client_name = project.client.name if project.client else None
    resp.workers = [UserResponse.model_validate(w) for w in project.workers]
    resp.delivery_phase = getattr(project, "delivery_phase", None) or 5
    resp.delivery_phase_name = phase_name(resp.delivery_phase)
    resp.engagement_id = getattr(project, "engagement_id", None)
    resp.development_days = getattr(project, "development_days", None)
    resp.advance_paise = 0
    resp.advance_invoice_id = getattr(project, "advance_invoice_id", None)
    resp.balance_invoice_id = getattr(project, "balance_invoice_id", None)
    resp.go_live_at = getattr(project, "go_live_at", None)
    budget = int(getattr(project, "budget_paise", 0) or 0)
    resp.balance_paise = max(budget - int(invoiced_paise or 0), 0)
    invoiced = int(invoiced_paise or 0)
    resp.invoiced_paise = invoiced
    resp.remaining_paise = max(budget - invoiced, 0)

    # Maintenance billing status
    if getattr(project, "recurring_invoices", None) and len(project.recurring_invoices) > 0:
        rec = project.recurring_invoices[0]
        resp.maintenance_status = "active" if rec.active else "paused"
        resp.maintenance_amount_paise = rec.amount_paise
        resp.maintenance_send_day = rec.send_day
        resp.maintenance_due_after_days = rec.due_after_days
        resp.maintenance_next_run_date = rec.next_run_date
    else:
        resp.maintenance_status = "none"

    # Hide budget/advance from worker role
    if role == "worker":
        resp.budget_paise = None
        resp.advance_paise = None
        resp.balance_paise = None
        resp.invoiced_paise = None
        resp.remaining_paise = None
        resp.maintenance_amount_paise = None
    return resp


async def _project_invoiced_map(db: AsyncSession, project_ids: List[str]) -> dict:
    """Sum ex-GST project-invoice subtots per project (all statuses)."""
    if not project_ids:
        return {}
    rows = (
        await db.execute(
            select(Invoice.project_id, func.coalesce(func.sum(Invoice.subtotal_paise), 0))
            .where(
                Invoice.project_id.in_(project_ids),
                Invoice.ledger == "development",
                Invoice.status.notin_(["cancelled", "void"]),
            )
            .group_by(Invoice.project_id)
        )
    ).all()
    return {pid: int(total or 0) for pid, total in rows}


async def _sanitize_project(
    db: AsyncSession, project: Project, role: str
) -> ProjectResponse:
    invoiced_map = await _project_invoiced_map(db, [project.id])
    return sanitize_project_for_role(
        project, role, invoiced_paise=invoiced_map.get(project.id, 0)
    )


async def _load_project(db: AsyncSession, project_id: str, *extra_opts) -> Project:
    opts = [
        selectinload(Project.client),
        selectinload(Project.workers),
        selectinload(Project.recurring_invoices),
        *extra_opts,
    ]
    stmt = select(Project).options(*opts).where(Project.id == project_id)
    project = (await db.execute(stmt)).scalars().first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


async def _assert_project_access(project: Project, user: User, *, write: bool = False) -> None:
    if is_privileged(user.role):
        return
    if user.role == "worker":
        if user.id not in [w.id for w in project.workers]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return
    if user.role == "client":
        if write or project.client_id != user.client_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

@router.get("", response_model=List[ProjectResponse])
async def list_projects(
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = (
        select(Project)
        .options(
            selectinload(Project.client),
            selectinload(Project.workers),
            selectinload(Project.recurring_invoices)
        )
        .order_by(Project.created_at.desc())
    )

    if current_user.role == "worker":
        # Workers see only projects assigned to them
        stmt = stmt.join(Project.workers).where(User.id == current_user.id)
    elif current_user.role == "client":
        # Clients see only their projects
        if not current_user.client_id:
            return []
        stmt = stmt.where(Project.client_id == current_user.client_id)

    if status_filter and status_filter != "all":
        stmt = stmt.where(Project.status == status_filter)

    res = await db.execute(stmt)
    projects = res.scalars().all()
    invoiced_map = await _project_invoiced_map(db, [p.id for p in projects])
    return [
        sanitize_project_for_role(
            p, current_user.role, invoiced_paise=invoiced_map.get(p.id, 0)
        )
        for p in projects
    ]

@router.get("/{project_id}/money")
async def project_money(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Live development and maintenance figures. Invoices are the only source."""
    from ..services.project_money import summarize_invoices

    project = await _load_project(db, project_id)
    await _assert_project_access(project, current_user)
    invoices = (
        await db.execute(select(Invoice).where(Invoice.project_id == project.id))
    ).scalars().all()
    summary = summarize_invoices(project, invoices)
    if current_user.role == "worker":
        return {"development": None, "maintenance": None, "variance": None}
    return summary


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = (
        select(Project)
        .options(
            selectinload(Project.client),
            selectinload(Project.workers),
            selectinload(Project.recurring_invoices)
        )
        .where(Project.id == project_id)
    )
    res = await db.execute(stmt)
    project = res.scalars().first()

    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if current_user.role == "worker":
        worker_ids = [w.id for w in project.workers]
        if current_user.id not in worker_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    elif current_user.role == "client":
        if project.client_id != current_user.client_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    return await _sanitize_project(db, project, current_user.role)

@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    # Verify client exists
    c_res = await db.execute(select(Client).where(Client.id == payload.client_id))
    if not c_res.scalars().first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Client not found")

    project = Project(
        name=payload.name,
        client_id=payload.client_id,
        description=payload.description,
        start_date=None,
        end_date=None,
        budget_paise=payload.budget_paise or 0,
        development_days=payload.development_days,
        status="lead",
        delivery_phase=5,
    )
    db.add(project)
    await db.flush()
    await ensure_project_checklist(db, project, done_through_stage=0)

    if payload.worker_ids:
        for w_id in payload.worker_ids:
            w_res = await db.execute(select(User).where(User.id == w_id, User.role == "worker"))
            if w_res.scalars().first():
                db.add(ProjectWorker(project_id=project.id, user_id=w_id))

    # Maintenance is saved here. The first bill is created on Go Live, not on create.
    if payload.maintenance and payload.maintenance.amount_paise > 0:
        m = payload.maintenance
        send_day = min(max(1, m.send_day), 28)

        rec = RecurringInvoice(
            client_id=payload.client_id,
            project_id=project.id,
            amount_paise=m.amount_paise,
            send_day=send_day,
            due_after_days=m.due_after_days or 5,
            reminder_every_days=m.reminder_every_days or 2,
            starts_on=date.today(),
            ends_on=m.ends_on,
            day_of_month=send_day,
            next_run_date=date(2099, 1, 1),
            active=True,
            notes=m.notes or f"Monthly maintenance for {payload.name}",
            line_items_template=[{
                "description": f"{payload.name} - Monthly Maintenance",
                "quantity": 1,
                "unit_rate_paise": m.amount_paise
            }]
        )
        db.add(rec)

    await db.commit()

    # Re-fetch with relationships
    stmt = (
        select(Project)
        .options(
            selectinload(Project.client),
            selectinload(Project.workers),
            selectinload(Project.recurring_invoices)
        )
        .where(Project.id == project.id)
    )
    res = await db.execute(stmt)
    created_prj = res.scalars().first()
    return sanitize_project_for_role(created_prj, "founder_ceo")


@router.post("/{project_id}/start", response_model=ProjectResponse)
async def start_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    """Start a not-started project: today + development days becomes the deadline."""
    project = await _load_project(db, project_id)
    if project.status != "lead":
        raise HTTPException(status_code=400, detail="This project has already started")
    days = int(project.development_days or 0)
    if days <= 0:
        raise HTTPException(
            status_code=400,
            detail="Set the development duration before starting this project",
        )
    today = date.today()
    project.start_date = today
    project.end_date = today + timedelta(days=days)
    project.status = "running"
    await db.commit()
    project = await _load_project(db, project_id)
    return sanitize_project_for_role(project, "founder_ceo")


@router.get("/{project_id}/documents", response_model=List[ProjectDocumentResponse])
async def list_project_documents(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _load_project(db, project_id)
    await _assert_project_access(project, current_user)
    rows = (
        await db.execute(
            select(ProjectDocument)
            .where(ProjectDocument.project_id == project_id)
            .order_by(ProjectDocument.created_at.desc())
        )
    ).scalars().all()
    return [ProjectDocumentResponse.model_validate(row) for row in rows]


@router.post("/{project_id}/documents", response_model=ProjectDocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_project_document(
    project_id: str,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    doc_kind: str = Form("other"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    project = await _load_project(db, project_id)
    kind = doc_kind if doc_kind in DOC_KINDS else "other"
    stored, original, content_type, size = await save_project_upload(project.id, file)
    label = (title or "").strip() or {
        "quotation": "Quotation",
        "agreement": "Agreement",
        "other": "Other",
    }[kind]
    doc = ProjectDocument(
        project_id=project.id,
        title=label,
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
    return ProjectDocumentResponse.model_validate(doc)


@router.get("/{project_id}/documents/{doc_id}/file")
async def download_project_document(
    project_id: str,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _load_project(db, project_id)
    await _assert_project_access(project, current_user)
    doc = (
        await db.execute(
            select(ProjectDocument).where(
                ProjectDocument.id == doc_id,
                ProjectDocument.project_id == project_id,
            )
        )
    ).scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    data = read_stored(project_id, doc.stored_filename)
    filename = (doc.original_filename or "document").replace('"', "")
    return Response(
        content=data,
        media_type=doc.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.delete("/{project_id}/documents/{doc_id}", status_code=status.HTTP_200_OK)
async def delete_project_document(
    project_id: str,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    doc = (
        await db.execute(
            select(ProjectDocument).where(
                ProjectDocument.id == doc_id,
                ProjectDocument.project_id == project_id,
            )
        )
    ).scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    delete_stored(project_id, doc.stored_filename)
    await db.delete(doc)
    await db.commit()
    return {"message": "Document deleted"}


@router.post("/{project_id}/maintenance/toggle", response_model=ProjectResponse)
async def toggle_project_maintenance(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    stmt = (
        select(Project)
        .options(
            selectinload(Project.client),
            selectinload(Project.workers),
            selectinload(Project.recurring_invoices)
        )
        .where(Project.id == project_id)
    )
    res = await db.execute(stmt)
    project = res.scalars().first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if not project.recurring_invoices or len(project.recurring_invoices) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No recurring maintenance configured for this project")

    rec = project.recurring_invoices[0]
    rec.active = not rec.active
    await db.commit()
    await db.refresh(rec)
    return sanitize_project_for_role(project, "founder_ceo")


@router.post("/{project_id}/go-live", response_model=ProjectProrataResponse)
async def go_live_project(
    project_id: str,
    payload: ProjectGoLiveRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    """Idempotent Go Live: set go_live_at, create+email prorata AMC (inclusive of go-live day)."""
    from ..services.scheduler import _dispatch_invoice_email

    project = await _load_project(db, project_id)
    go_live_date = payload.go_live_date or date.today()

    # Already live → return existing stub, do not re-email
    if project.go_live_at:
        period_key = f"{project.go_live_at.year}-{project.go_live_at.month:02d}"
        existing_id = None
        if project.recurring_invoices:
            existing_id = await db.scalar(
                select(Invoice.id).where(
                    Invoice.recurring_id == project.recurring_invoices[0].id,
                    Invoice.period_key == period_key,
                )
            )
        resp = sanitize_project_for_role(project, "founder_ceo")
        if existing_id:
            resp.maintenance_prorata_invoice_id = existing_id
        return ProjectProrataResponse(
            created=False,
            skipped=False,
            emailed=False,
            reason="Already went live — prorata was not resent",
            invoice_id=existing_id,
            period_key=period_key,
            next_run_date=(
                project.recurring_invoices[0].next_run_date if project.recurring_invoices else None
            ),
            project=resp,
        )

    project.go_live_at = go_live_date
    inv = None
    calc = None
    emailed = False
    send_day = 1

    if project.recurring_invoices and (project.recurring_invoices[0].amount_paise or 0) > 0:
        rec = project.recurring_invoices[0]
        if not rec.active:
            raise HTTPException(status_code=400, detail="Maintenance is paused — activate before Go Live")
        send_day = min(int(rec.send_day or 1), 28)
        inv, calc = await create_prorata_invoice(db, rec, go_live_date, commit=False)
    else:
        # No AMC configured — still mark go-live
        calc = None

    await db.commit()

    if inv:
        send_on = date(go_live_date.year, go_live_date.month, send_day)
        if send_on <= date.today():
            emailed = await _dispatch_invoice_email(inv.id, is_new=True)

    project = await _load_project(db, project_id)
    resp = sanitize_project_for_role(project, "founder_ceo")
    if inv:
        resp.maintenance_prorata_invoice_id = inv.id

    return ProjectProrataResponse(
        created=inv is not None and calc is not None and not calc.skipped,
        skipped=bool(calc and calc.skipped) or (calc is None and not inv),
        emailed=emailed,
        reason=(
            None
            if inv
            else (
                "No monthly maintenance configured — go-live recorded only"
                if not project.recurring_invoices
                else "Prorata amount was zero"
            )
        ),
        invoice_id=inv.id if inv else None,
        invoice_number=inv.invoice_number if inv else None,
        period_key=calc.period_key if calc else None,
        billable_days=calc.billable_days if calc else None,
        days_in_month=calc.days_in_month if calc else None,
        amount_paise=calc.amount_paise if calc and inv else 0,
        next_run_date=(
            project.recurring_invoices[0].next_run_date if project.recurring_invoices else None
        ),
        project=resp,
    )


@router.post("/{project_id}/invoices/advance", response_model=ProjectInvoiceCreateResponse)
async def create_project_advance_invoice(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    project = await _load_project(db, project_id)
    raise HTTPException(status_code=400, detail="Create a development invoice from the invoice form and enter the amount yourself")

    if project.advance_invoice_id:
        existing = (
            await db.execute(select(Invoice).where(Invoice.id == project.advance_invoice_id))
        ).scalars().first()
        if existing:
            return ProjectInvoiceCreateResponse(
                invoice_id=existing.id,
                invoice_number=existing.invoice_number,
                total_paise=existing.total_paise,
                status=existing.status,
                kind="advance",
                project=sanitize_project_for_role(project, "founder_ceo"),
            )

    inv = await create_project_amount_invoice(
        db,
        client_id=project.client_id,
        project_id=project.id,
        amount_paise=int(project.advance_paise),
        description=f"Project advance — {project.name}",
        notes=f"Development advance · {project.name}",
    )
    project.advance_invoice_id = inv.id
    await db.commit()
    project = await _load_project(db, project_id)
    return ProjectInvoiceCreateResponse(
        invoice_id=inv.id,
        invoice_number=inv.invoice_number,
        total_paise=inv.total_paise,
        status=inv.status,
        kind="advance",
        project=sanitize_project_for_role(project, "founder_ceo"),
    )


@router.post("/{project_id}/invoices/balance", response_model=ProjectInvoiceCreateResponse)
async def create_project_balance_invoice(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    project = await _load_project(db, project_id)
    balance = int(project.budget_paise or 0)
    if balance <= 0:
        raise HTTPException(status_code=400, detail="No development balance remaining")

    if project.balance_invoice_id:
        existing = (
            await db.execute(select(Invoice).where(Invoice.id == project.balance_invoice_id))
        ).scalars().first()
        if existing:
            return ProjectInvoiceCreateResponse(
                invoice_id=existing.id,
                invoice_number=existing.invoice_number,
                total_paise=existing.total_paise,
                status=existing.status,
                kind="balance",
                project=sanitize_project_for_role(project, "founder_ceo"),
            )

    inv = await create_project_amount_invoice(
        db,
        client_id=project.client_id,
        project_id=project.id,
        amount_paise=balance,
        description=f"Development balance — {project.name}",
        notes=f"Development balance (total − advance) · {project.name}",
        due_after_days=5,
        invoice_type="balance",
    )
    project.balance_invoice_id = inv.id
    await db.commit()
    project = await _load_project(db, project_id)
    return ProjectInvoiceCreateResponse(
        invoice_id=inv.id,
        invoice_number=inv.invoice_number,
        total_paise=inv.total_paise,
        status=inv.status,
        kind="balance",
        project=sanitize_project_for_role(project, "founder_ceo"),
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = (
        select(Project)
        .options(
            selectinload(Project.client),
            selectinload(Project.workers),
            selectinload(Project.recurring_invoices),
        )
        .where(Project.id == project_id)
    )
    res = await db.execute(stmt)
    project = res.scalars().first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if current_user.role == "worker":
        worker_ids = [w.id for w in project.workers]
        if current_user.id not in worker_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

        if payload.status:
            project.status = payload.status
        await db.commit()
        await db.refresh(project)
        return sanitize_project_for_role(project, current_user.role)

    elif current_user.role == "client":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Clients cannot edit projects")

    update_data = payload.model_dump(exclude_unset=True)
    worker_ids = update_data.pop("worker_ids", None)
    maintenance_amount = update_data.pop("maintenance_amount_paise", None)
    update_data.pop("advance_paise", None)

    for k, v in update_data.items():
        setattr(project, k, v)

    # Timeline starts only when a start date is set by hand.
    if "start_date" in update_data and not project.start_date:
        project.end_date = None
        if project.status == "running":
            project.status = "lead"
    elif project.start_date and project.development_days and int(project.development_days) > 0:
        if "end_date" not in update_data:
            project.end_date = project.start_date + timedelta(days=int(project.development_days))
        if "start_date" in update_data and project.status == "lead":
            project.status = "running"

    if worker_ids is not None:
        await db.execute(delete(ProjectWorker).where(ProjectWorker.project_id == project.id))
        for w_id in worker_ids:
            w_res = await db.execute(select(User).where(User.id == w_id))
            if w_res.scalars().first():
                db.add(ProjectWorker(project_id=project.id, user_id=w_id))

    if maintenance_amount is not None:
        await upsert_project_maintenance(db, project, maintenance_amount)

    await db.commit()

    refreshed = await _load_project(db, project.id)
    return await _sanitize_project(db, refreshed, "founder_ceo")

@router.delete("/{project_id}", status_code=status.HTTP_200_OK)
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Project).where(Project.id == project_id))
    project = res.scalars().first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    await db.delete(project)
    await db.commit()
    return {"message": "Project deleted successfully"}


# ---------- Delivery (phases 05–11) ----------

@router.get("/{project_id}/delivery", response_model=ProjectDeliveryDetailResponse)
async def get_project_delivery(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _load_project(db, project_id, selectinload(Project.checklist_items))
    await _assert_project_access(project, current_user)
    await ensure_project_checklist(db, project, done_through_stage=0)
    await db.commit()
    project = await _load_project(db, project_id, selectinload(Project.checklist_items))

    base = sanitize_project_for_role(project, current_user.role)
    items = list(project.checklist_items or [])
    if current_user.role == "client":
        # Clients see phase progress only — no internal checklist ticks
        items = []
    return ProjectDeliveryDetailResponse(
        **base.model_dump(),
        checklist_items=[ProjectChecklistItemResponse.model_validate(i) for i in items],
        checklist_progress_percent=checklist_progress(
            list(project.checklist_items or []), project.delivery_phase
        ),
    )


@router.post("/{project_id}/checklist/{item_id}/toggle", response_model=ProjectDeliveryDetailResponse)
async def toggle_project_checklist(
    project_id: str,
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == "client":
        raise HTTPException(status_code=403, detail="Clients cannot update delivery checklist")
    project = await _load_project(db, project_id, selectinload(Project.checklist_items))
    await _assert_project_access(project, current_user)
    item = next((i for i in project.checklist_items if i.id == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    item.is_done = not item.is_done
    item.done_at = datetime.now(timezone.utc) if item.is_done else None
    item.done_by = current_user.id if item.is_done else None
    await sync_engagement_from_project(db, project)
    await db.commit()
    return await get_project_delivery(project_id, db, current_user)


@router.post("/{project_id}/delivery/advance", response_model=ProjectDeliveryDetailResponse)
async def advance_project_delivery(
    project_id: str,
    close_without_amc: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == "client":
        raise HTTPException(status_code=403, detail="Forbidden")
    project = await _load_project(db, project_id, selectinload(Project.checklist_items))
    await _assert_project_access(project, current_user)

    phase = project.delivery_phase or DELIVERY_MIN_STAGE
    stage_items = [i for i in (project.checklist_items or []) if i.stage_number == phase]
    if stage_items and not all(i.is_done for i in stage_items):
        raise HTTPException(status_code=400, detail="Complete current delivery checklist before advancing")

    if phase >= DELIVERY_MAX_STAGE:
        if close_without_amc or is_privileged(current_user.role):
            project.status = "completed"
            await sync_engagement_from_project(db, project)
            await db.commit()
            return await get_project_delivery(project_id, db, current_user)
        raise HTTPException(status_code=400, detail="Already at AMC phase")

    if phase == 10:
        # Set warranty on linked engagement if present
        if project.engagement_id:
            eng = (
                await db.execute(select(Engagement).where(Engagement.id == project.engagement_id))
            ).scalars().first()
            if eng and not eng.warranty_ends_on:
                eng.warranty_ends_on = date.today() + timedelta(days=WARRANTY_DEFAULT_DAYS)
        if close_without_amc:
            project.status = "completed"
            project.delivery_phase = 10
            await sync_engagement_from_project(db, project)
            await db.commit()
            return await get_project_delivery(project_id, db, current_user)

    project.delivery_phase = min(phase + 1, DELIVERY_MAX_STAGE)
    await sync_engagement_from_project(db, project)
    await db.commit()
    return await get_project_delivery(project_id, db, current_user)


@router.post("/{project_id}/delivery/regress", response_model=ProjectDeliveryDetailResponse)
async def regress_project_delivery(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    project = await _load_project(db, project_id, selectinload(Project.checklist_items))
    if project.delivery_phase <= DELIVERY_MIN_STAGE:
        raise HTTPException(status_code=400, detail="Already at onboarding phase")
    project.delivery_phase -= 1
    if project.status == "completed":
        project.status = "running"
    await sync_engagement_from_project(db, project)
    await db.commit()
    return await get_project_delivery(project_id, db, current_user)


# ---------- Additional features + docs ----------

def _feature_to_response(feat: ProjectFeature, role: str) -> ProjectFeatureResponse:
    resp = ProjectFeatureResponse.model_validate(feat)
    resp.docs = [ProjectFeatureDocResponse.model_validate(d) for d in (feat.docs or [])]
    if role == "worker":
        pass
    if role == "client":
        resp.quoted_paise = feat.quoted_paise  # agreed add-on value ok to show
    return resp


@router.get("/{project_id}/features", response_model=List[ProjectFeatureResponse])
async def list_project_features(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _load_project(db, project_id)
    await _assert_project_access(project, current_user)
    stmt = (
        select(ProjectFeature)
        .options(selectinload(ProjectFeature.docs))
        .where(ProjectFeature.project_id == project_id)
        .order_by(ProjectFeature.created_at.desc())
    )
    feats = (await db.execute(stmt)).scalars().all()
    if current_user.role == "client":
        feats = [f for f in feats if f.status in CLIENT_VISIBLE_FEATURE_STATUSES]
    return [_feature_to_response(f, current_user.role) for f in feats]


@router.post("/{project_id}/features", response_model=ProjectFeatureResponse, status_code=status.HTTP_201_CREATED)
async def create_project_feature(
    project_id: str,
    payload: ProjectFeatureCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    project = await _load_project(db, project_id)
    status_val = payload.status if payload.status in FEATURE_STATUSES else "requested"
    feat = ProjectFeature(
        project_id=project.id,
        title=payload.title,
        description=payload.description,
        status=status_val,
        quoted_paise=payload.quoted_paise,
    )
    db.add(feat)
    await db.commit()
    feat = (
        await db.execute(
            select(ProjectFeature).options(selectinload(ProjectFeature.docs)).where(ProjectFeature.id == feat.id)
        )
    ).scalars().first()
    return _feature_to_response(feat, current_user.role)


@router.patch("/{project_id}/features/{feature_id}", response_model=ProjectFeatureResponse)
async def update_project_feature(
    project_id: str,
    feature_id: str,
    payload: ProjectFeatureUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _load_project(db, project_id)
    await _assert_project_access(project, current_user)
    if current_user.role == "client":
        raise HTTPException(status_code=403, detail="Forbidden")

    feat = (
        await db.execute(
            select(ProjectFeature)
            .options(selectinload(ProjectFeature.docs))
            .where(ProjectFeature.id == feature_id, ProjectFeature.project_id == project_id)
        )
    ).scalars().first()
    if not feat:
        raise HTTPException(status_code=404, detail="Feature not found")

    data = payload.model_dump(exclude_unset=True)
    if current_user.role == "worker":
        # Workers may only update status
        data = {k: v for k, v in data.items() if k == "status"}
    if "status" in data and data["status"] not in FEATURE_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid feature status")
    for k, v in data.items():
        setattr(feat, k, v)
    await db.commit()
    await db.refresh(feat)
    feat = (
        await db.execute(
            select(ProjectFeature).options(selectinload(ProjectFeature.docs)).where(ProjectFeature.id == feature_id)
        )
    ).scalars().first()
    return _feature_to_response(feat, current_user.role)


@router.delete("/{project_id}/features/{feature_id}", status_code=status.HTTP_200_OK)
async def delete_project_feature(
    project_id: str,
    feature_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    feat = (
        await db.execute(
            select(ProjectFeature).where(ProjectFeature.id == feature_id, ProjectFeature.project_id == project_id)
        )
    ).scalars().first()
    if not feat:
        raise HTTPException(status_code=404, detail="Feature not found")
    await db.delete(feat)
    await db.commit()
    return {"message": "Feature deleted"}


@router.post(
    "/{project_id}/features/{feature_id}/docs",
    response_model=ProjectFeatureDocResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_feature_doc(
    project_id: str,
    feature_id: str,
    payload: ProjectFeatureDocCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    feat = (
        await db.execute(
            select(ProjectFeature).where(ProjectFeature.id == feature_id, ProjectFeature.project_id == project_id)
        )
    ).scalars().first()
    if not feat:
        raise HTTPException(status_code=404, detail="Feature not found")
    doc_type = payload.doc_type if payload.doc_type in ("note", "link", "spec") else "note"
    doc = ProjectFeatureDoc(
        feature_id=feature_id,
        title=payload.title,
        body_or_url=payload.body_or_url,
        doc_type=doc_type,
        created_by=current_user.id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return ProjectFeatureDocResponse.model_validate(doc)


@router.delete("/{project_id}/features/{feature_id}/docs/{doc_id}", status_code=status.HTTP_200_OK)
async def delete_feature_doc(
    project_id: str,
    feature_id: str,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    doc = (
        await db.execute(
            select(ProjectFeatureDoc)
            .join(ProjectFeature)
            .where(
                ProjectFeatureDoc.id == doc_id,
                ProjectFeatureDoc.feature_id == feature_id,
                ProjectFeature.project_id == project_id,
            )
        )
    ).scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Doc not found")
    await db.delete(doc)
    await db.commit()
    return {"message": "Doc deleted"}

