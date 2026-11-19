from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..core.dependencies import get_db, get_current_user, is_privileged
from ..core.database import not_deleted
from ..models import Note, User
from ..schemas import NoteResponse, NoteCreate

router = APIRouter(prefix="/notes", tags=["Notes"])

@router.get("", response_model=List[NoteResponse])
async def list_notes(
    entity_type: str = Query(..., pattern="^(client|lead)$"),
    entity_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if entity_type == "lead" and not is_privileged(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if current_user.role == "client" and (
        entity_type != "client" or current_user.client_id != entity_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if current_user.role not in ("client", "worker") and not is_privileged(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    stmt = (
        select(Note)
        .options(selectinload(Note.author))
        .where(Note.entity_type == entity_type, Note.entity_id == entity_id, not_deleted(Note))
        .order_by(Note.created_at.desc())
    )
    res = await db.execute(stmt)
    notes = res.scalars().all()

    output = []
    for n in notes:
        resp = NoteResponse.model_validate(n)
        resp.author_name = n.author.full_name if n.author else "System"
        output.append(resp)
    return output

@router.post("", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def create_note(
    payload: NoteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if payload.entity_type not in ["client", "lead"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid entity type")

    if payload.entity_type == "lead" and not is_privileged(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only founder can add notes to leads")

    if current_user.role == "client" and (
        payload.entity_type != "client" or payload.entity_id != current_user.client_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if current_user.role not in ("client", "worker") and not is_privileged(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    note = Note(
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        author_id=current_user.id,
        body=payload.body
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)

    resp = NoteResponse.model_validate(note)
    resp.author_name = current_user.full_name
    return resp

@router.delete("/{note_id}", status_code=status.HTTP_200_OK)
async def delete_note(
    note_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    res = await db.execute(select(Note).where(Note.id == note_id, not_deleted(Note)))
    note = res.scalars().first()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    if not is_privileged(current_user.role) and note.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    note.soft_delete()
    await db.commit()
    return {"message": "Note deleted successfully"}
