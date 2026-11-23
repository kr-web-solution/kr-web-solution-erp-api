from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from ..core.dependencies import get_db, get_current_user, require_founder_ceo, is_privileged
from ..core.database import not_deleted
from ..models import Client, Project, ProjectClient, Invoice, User
from ..schemas import (
    ClientResponse,
    ClientCreate,
    ClientUpdate,
    ClientListResponse,
    OptionsResponse,
    OptionItemSchema,
)
from ..services.audit import audit_create, audit_soft_delete, audit_update, snapshot_entity, write_audit
from ..services.client_project import delete_client as delete_client_record
from ..services.pagination import (
    build_envelope,
    clamp_options_limit,
    clamp_page,
    clamp_page_size,
    offset_for,
)

router = APIRouter(prefix="/clients", tags=["Clients"])


async def _projects_count_for_client(db: AsyncSession, client_id: str) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(ProjectClient)
            .join(Project, Project.id == ProjectClient.project_id)
            .where(ProjectClient.client_id == client_id, not_deleted(Project))
        )
        or 0
    )


_BLANK_TO_NONE = (
    "primary_contact_name",
    "designation",
    "contact_email",
    "contact_phone",
    "email",
    "phone",
    "website",
    "address",
    "city",
    "state",
    "pincode",
    "gstin",
    "pan",
    "state_code",
    "notes",
)


def _clean_client_payload(data: dict, *, fill_billing_email: bool = False) -> dict:
    for key in _BLANK_TO_NONE:
        if isinstance(data.get(key), str) and not data[key].strip():
            data[key] = None
    if isinstance(data.get("name"), str):
        data["name"] = data["name"].strip()
    # Prefer an explicit company email. Do not silently promote contact_email to To —
    # contact person is CC'd on invoice mail instead.
    if fill_billing_email and not data.get("email") and data.get("contact_email"):
        data["email"] = data["contact_email"]
    return data


async def _assert_unique_active_name(
    db: AsyncSession, name: str, *, exclude_id: Optional[str] = None
) -> None:
    from sqlalchemy import func

    stmt = select(Client).where(
        func.lower(Client.name) == name.strip().lower(),
        not_deleted(Client),
        Client.is_active == True,  # noqa: E712
    )
    if exclude_id:
        stmt = stmt.where(Client.id != exclude_id)
    existing = (await db.execute(stmt)).scalars().first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An active client named '{name.strip()}' already exists",
        )

async def _batched_client_metrics(db: AsyncSession, client_ids: list[str]) -> dict[str, dict]:
    """One round-trip each for project counts, invoice counts, outstanding (no N+1)."""
    metrics = {
        cid: {"projects_count": 0, "invoices_count": 0, "outstanding_paise": 0}
        for cid in client_ids
    }
    if not client_ids:
        return metrics

    proj_rows = (
        await db.execute(
            select(ProjectClient.client_id, func.count())
            .join(Project, Project.id == ProjectClient.project_id)
            .where(ProjectClient.client_id.in_(client_ids), not_deleted(Project))
            .group_by(ProjectClient.client_id)
        )
    ).all()
    for cid, cnt in proj_rows:
        metrics[cid]["projects_count"] = int(cnt or 0)

    inv_rows = (
        await db.execute(
            select(Invoice.client_id, func.count())
            .where(Invoice.client_id.in_(client_ids), not_deleted(Invoice))
            .group_by(Invoice.client_id)
        )
    ).all()
    for cid, cnt in inv_rows:
        metrics[cid]["invoices_count"] = int(cnt or 0)

    # Match prior UI: sum total_paise for status sent/overdue (DB statuses).
    out_rows = (
        await db.execute(
            select(Invoice.client_id, func.coalesce(func.sum(Invoice.total_paise), 0))
            .where(
                Invoice.client_id.in_(client_ids),
                not_deleted(Invoice),
                Invoice.status.in_(("sent", "overdue")),
            )
            .group_by(Invoice.client_id)
        )
    ).all()
    for cid, total in out_rows:
        metrics[cid]["outstanding_paise"] = int(total or 0)

    return metrics


@router.get("", response_model=ClientListResponse)
async def list_clients(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("name"),
    sort_order: Optional[str] = Query("asc"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    page = clamp_page(page)
    page_size = clamp_page_size(page_size)

    if current_user.role == "client":
        if not current_user.client_id:
            return ClientListResponse(**build_envelope([], page=1, page_size=page_size, total_items=0))
        stmt = select(Client).where(Client.id == current_user.client_id, not_deleted(Client))
    elif current_user.role == "worker":
        stmt = (
            select(Client)
            .join(ProjectClient, ProjectClient.client_id == Client.id)
            .join(Project, Project.id == ProjectClient.project_id)
            .join(Project.workers)
            .where(User.id == current_user.id, not_deleted(Client), not_deleted(Project))
            .distinct()
        )
    elif not is_privileged(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    else:
        stmt = select(Client).where(not_deleted(Client))

    if search and search.strip():
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Client.name.ilike(term),
                Client.primary_contact_name.ilike(term),
                Client.email.ilike(term),
                Client.contact_email.ilike(term),
                Client.phone.ilike(term),
                Client.gstin.ilike(term),
            )
        )

    sort_map = {
        "name": Client.name,
        "created_at": Client.created_at,
        "updated_at": Client.updated_at,
    }
    sort_col = sort_map.get((sort_by or "name").strip(), Client.name)
    order = sort_col.desc() if (sort_order or "asc").lower() == "desc" else sort_col.asc()

    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total_items = int(await db.scalar(count_stmt) or 0)

    res = await db.execute(
        stmt.order_by(order).offset(offset_for(page, page_size)).limit(page_size)
    )
    clients = res.scalars().unique().all()
    metrics = await _batched_client_metrics(db, [c.id for c in clients])

    response_list = []
    for cl in clients:
        m = metrics.get(cl.id) or {}
        resp = ClientResponse.model_validate(cl)
        resp.projects_count = m.get("projects_count", 0)
        resp.invoices_count = m.get("invoices_count", 0)
        resp.outstanding_paise = m.get("outstanding_paise", 0)
        response_list.append(resp)

    return ClientListResponse(
        **build_envelope(response_list, page=page, page_size=page_size, total_items=total_items)
    )


@router.get("/options", response_model=OptionsResponse)
async def list_client_options(
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lim = clamp_options_limit(limit)
    if current_user.role == "client":
        if not current_user.client_id:
            return OptionsResponse(items=[])
        stmt = select(Client).where(Client.id == current_user.client_id, not_deleted(Client))
    elif current_user.role == "worker":
        stmt = (
            select(Client)
            .join(ProjectClient, ProjectClient.client_id == Client.id)
            .join(Project, Project.id == ProjectClient.project_id)
            .join(Project.workers)
            .where(User.id == current_user.id, not_deleted(Client), not_deleted(Project))
            .distinct()
        )
    elif not is_privileged(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    else:
        stmt = select(Client).where(not_deleted(Client), Client.is_active == True)  # noqa: E712

    if search and search.strip():
        term = f"%{search.strip()}%"
        stmt = stmt.where(Client.name.ilike(term))

    rows = (
        await db.execute(stmt.order_by(Client.name.asc()).limit(lim))
    ).scalars().unique().all()
    return OptionsResponse(
        items=[OptionItemSchema(id=c.id, label=c.name, meta={"gstin": c.gstin}) for c in rows]
    )

@router.get("/{client_id}", response_model=ClientResponse)
async def get_client(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role == "client":
        if current_user.client_id != client_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    elif current_user.role == "worker":
        assigned = await db.scalar(
            select(ProjectClient.client_id)
            .join(Project, Project.id == ProjectClient.project_id)
            .join(Project.workers)
            .where(
                User.id == current_user.id,
                ProjectClient.client_id == client_id,
                not_deleted(Project),
            )
            .limit(1)
        )
        if assigned is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    elif not is_privileged(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    res = await db.execute(select(Client).where(Client.id == client_id, not_deleted(Client)))
    client = res.scalars().first()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    metrics = await _batched_client_metrics(db, [client.id])
    m = metrics.get(client.id) or {}
    resp = ClientResponse.model_validate(client)
    resp.projects_count = m.get("projects_count", 0)
    resp.invoices_count = m.get("invoices_count", 0)
    resp.outstanding_paise = m.get("outstanding_paise", 0)
    return resp

@router.post("", response_model=ClientResponse, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClientCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    data = _clean_client_payload(payload.model_dump(), fill_billing_email=True)
    if not (data.get("name") or "").strip():
        raise HTTPException(status_code=422, detail="Client name is required")
    await _assert_unique_active_name(db, data["name"])
    client = Client(**data, is_active=True)
    db.add(client)
    await db.flush()
    await audit_create(db, client, actor_id=current_user.id, request=request)
    await db.commit()
    await db.refresh(client)
    resp = ClientResponse.model_validate(client)
    resp.projects_count = 0
    resp.invoices_count = 0
    return resp

@router.patch("/{client_id}", response_model=ClientResponse)
async def update_client(
    client_id: str,
    payload: ClientUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Client).where(Client.id == client_id, not_deleted(Client)))
    client = res.scalars().first()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    before = snapshot_entity(client)
    data = _clean_client_payload(payload.model_dump(exclude_unset=True))
    if "name" in data:
        if not (data.get("name") or "").strip():
            raise HTTPException(status_code=422, detail="Client name is required")
        await _assert_unique_active_name(db, data["name"], exclude_id=client.id)
    for k, v in data.items():
        setattr(client, k, v)

    await audit_update(db, client, before, actor_id=current_user.id, request=request)
    await db.commit()
    await db.refresh(client)

    p_cnt = await _projects_count_for_client(db, client.id)
    i_cnt = await db.scalar(
        select(func.count()).select_from(Invoice).where(Invoice.client_id == client.id, not_deleted(Invoice))
    )
    resp = ClientResponse.model_validate(client)
    resp.projects_count = p_cnt
    resp.invoices_count = i_cnt or 0
    return resp


@router.post("/{client_id}/deactivate", response_model=ClientResponse)
async def deactivate_client(
    client_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    res = await db.execute(select(Client).where(Client.id == client_id, not_deleted(Client)))
    client = res.scalars().first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    client.is_active = False
    await write_audit(
        db,
        current_user,
        "clients",
        client.id,
        "update",
        after={"is_active": False},
        request=request,
        client_id=client.id,
    )
    await db.commit()
    await db.refresh(client)
    return ClientResponse.model_validate(client)


@router.post("/{client_id}/activate", response_model=ClientResponse)
async def activate_client(
    client_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    res = await db.execute(select(Client).where(Client.id == client_id, not_deleted(Client)))
    client = res.scalars().first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    await _assert_unique_active_name(db, client.name, exclude_id=client.id)
    client.is_active = True
    await write_audit(
        db,
        current_user,
        "clients",
        client.id,
        "update",
        after={"is_active": True},
        request=request,
        client_id=client.id,
    )
    await db.commit()
    await db.refresh(client)
    return ClientResponse.model_validate(client)

@router.delete("/{client_id}", status_code=status.HTTP_200_OK)
async def delete_client(
    client_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    client = await delete_client_record(db, client_id)
    await audit_soft_delete(db, client, actor_id=current_user.id, request=request)
    await db.commit()
    return {"message": "Client deleted successfully"}

