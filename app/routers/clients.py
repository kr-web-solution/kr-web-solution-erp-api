from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from ..core.dependencies import get_db, get_current_user, require_founder_ceo, is_privileged
from ..core.database import not_deleted
from ..models import Client, Project, ProjectClient, Invoice, User
from ..schemas import ClientResponse, ClientCreate, ClientUpdate
from ..services.audit import audit_create, audit_soft_delete, audit_update, snapshot_entity, write_audit
from ..services.client_project import delete_client as delete_client_record

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

@router.get("", response_model=List[ClientResponse])
async def list_clients(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role == "client":
        if not current_user.client_id:
            return []
        stmt = select(Client).where(Client.id == current_user.client_id, not_deleted(Client))
    elif current_user.role == "worker":
        # Workers see clients of projects assigned to them
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
        # founder_ceo / admin sees all
        stmt = select(Client).where(not_deleted(Client)).order_by(Client.name.asc())

    res = await db.execute(stmt)
    clients = res.scalars().all()

    # Calculate counts
    response_list = []
    for cl in clients:
        p_cnt = await _projects_count_for_client(db, cl.id)
        i_cnt = await db.scalar(
            select(func.count()).select_from(Invoice).where(Invoice.client_id == cl.id, not_deleted(Invoice))
        )
        resp = ClientResponse.model_validate(cl)
        resp.projects_count = p_cnt
        resp.invoices_count = i_cnt or 0
        response_list.append(resp)

    return response_list

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

    p_cnt = await _projects_count_for_client(db, client.id)
    i_cnt = await db.scalar(
        select(func.count()).select_from(Invoice).where(Invoice.client_id == client.id, not_deleted(Invoice))
    )
    resp = ClientResponse.model_validate(client)
    resp.projects_count = p_cnt
    resp.invoices_count = i_cnt or 0
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

