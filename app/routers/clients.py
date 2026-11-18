from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from ..core.dependencies import get_db, get_current_user, require_founder_ceo
from ..models import Client, Project, Invoice, User
from ..schemas import ClientResponse, ClientCreate, ClientUpdate

router = APIRouter(prefix="/clients", tags=["Clients"])

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
    "notes",
)


def _clean_client_payload(data: dict, *, fill_billing_email: bool = False) -> dict:
    for key in _BLANK_TO_NONE:
        if isinstance(data.get(key), str) and not data[key].strip():
            data[key] = None
    # Invoices are sent to the company email. Use the contact email when none is given.
    if fill_billing_email and not data.get("email") and data.get("contact_email"):
        data["email"] = data["contact_email"]
    return data

@router.get("", response_model=List[ClientResponse])
async def list_clients(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role == "client":
        if not current_user.client_id:
            return []
        stmt = select(Client).where(Client.id == current_user.client_id)
    elif current_user.role == "worker":
        # Workers see clients of projects assigned to them
        stmt = (
            select(Client)
            .join(Project, Project.client_id == Client.id)
            .join(Project.workers)
            .where(User.id == current_user.id)
            .distinct()
        )
    else:
        # founder_ceo sees all
        stmt = select(Client).order_by(Client.name.asc())

    res = await db.execute(stmt)
    clients = res.scalars().all()

    # Calculate counts
    response_list = []
    for cl in clients:
        p_cnt = await db.scalar(select(func.count()).select_from(Project).where(Project.client_id == cl.id))
        i_cnt = await db.scalar(select(func.count()).select_from(Invoice).where(Invoice.client_id == cl.id))
        resp = ClientResponse.model_validate(cl)
        resp.projects_count = p_cnt or 0
        resp.invoices_count = i_cnt or 0
        response_list.append(resp)

    return response_list

@router.get("/{client_id}", response_model=ClientResponse)
async def get_client(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role == "client" and current_user.client_id != client_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    res = await db.execute(select(Client).where(Client.id == client_id))
    client = res.scalars().first()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    p_cnt = await db.scalar(select(func.count()).select_from(Project).where(Project.client_id == client.id))
    i_cnt = await db.scalar(select(func.count()).select_from(Invoice).where(Invoice.client_id == client.id))
    resp = ClientResponse.model_validate(client)
    resp.projects_count = p_cnt or 0
    resp.invoices_count = i_cnt or 0
    return resp

@router.post("", response_model=ClientResponse, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClientCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    client = Client(**_clean_client_payload(payload.model_dump(), fill_billing_email=True))
    db.add(client)
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
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Client).where(Client.id == client_id))
    client = res.scalars().first()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    for k, v in _clean_client_payload(payload.model_dump(exclude_unset=True)).items():
        setattr(client, k, v)

    await db.commit()
    await db.refresh(client)

    p_cnt = await db.scalar(select(func.count()).select_from(Project).where(Project.client_id == client.id))
    i_cnt = await db.scalar(select(func.count()).select_from(Invoice).where(Invoice.client_id == client.id))
    resp = ClientResponse.model_validate(client)
    resp.projects_count = p_cnt or 0
    resp.invoices_count = i_cnt or 0
    return resp

@router.delete("/{client_id}", status_code=status.HTTP_200_OK)
async def delete_client(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Client).where(Client.id == client_id))
    client = res.scalars().first()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    await db.delete(client)
    await db.commit()
    return {"message": "Client deleted successfully"}

