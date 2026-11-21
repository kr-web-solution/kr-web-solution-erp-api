"""Helpers for project delivery phases, checklists, and engagement sync."""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..common.workflow import (
    DELIVERY_MIN_STAGE,
    MAINTENANCE_PHASE,
    STAGES_BY_NUMBER,
    build_project_delivery_checklist_seed,
)
from ..models import Engagement, Project, ProjectChecklistItem


def seed_project_checklist(project_id: str, done_through_stage: int = 0) -> List[ProjectChecklistItem]:
    rows: List[ProjectChecklistItem] = []
    now = datetime.now(timezone.utc)
    for stage_number, item_key, label, sort_order in build_project_delivery_checklist_seed():
        done = stage_number <= done_through_stage
        rows.append(
            ProjectChecklistItem(
                project_id=project_id,
                stage_number=stage_number,
                item_key=item_key,
                label=label,
                sort_order=sort_order,
                is_done=done,
                done_at=now if done else None,
            )
        )
    return rows


async def ensure_project_checklist(db: AsyncSession, project: Project, done_through_stage: int = 0) -> bool:
    """Seed delivery checklist if missing. Returns True when rows were inserted."""
    # Prefer already-loaded collection to avoid an extra round-trip on every Delivery open.
    unloaded = sa_inspect(project).unloaded
    if "checklist_items" not in unloaded and project.checklist_items:
        return False
    existing = (
        await db.execute(select(ProjectChecklistItem).where(ProjectChecklistItem.project_id == project.id).limit(1))
    ).scalars().first()
    if existing:
        return False
    for item in seed_project_checklist(project.id, done_through_stage=done_through_stage):
        db.add(item)
    await db.flush()
    return True


async def sync_engagement_from_project(db: AsyncSession, project: Project) -> None:
    """Mirror project delivery_phase onto linked engagement for Pipeline board accuracy."""
    if not project.engagement_id:
        # Try reverse link from engagement.project_id
        eng = (
            await db.execute(
                select(Engagement)
                .options(selectinload(Engagement.checklist_items))
                .where(Engagement.project_id == project.id)
            )
        ).scalars().first()
        if eng and not project.engagement_id:
            project.engagement_id = eng.id
    else:
        eng = (
            await db.execute(
                select(Engagement)
                .options(selectinload(Engagement.checklist_items))
                .where(Engagement.id == project.engagement_id)
            )
        ).scalars().first()

    if not eng:
        return

    eng.current_stage = project.delivery_phase
    if project.delivery_phase >= MAINTENANCE_PHASE:
        eng.status = "maintenance" if project.status != "completed" else "completed"
    elif eng.status not in ("lost", "on_hold", "completed"):
        eng.status = "active"

    # Mirror checklist done flags for stages <= delivery_phase on engagement delivery items
    phase = project.delivery_phase
    # Reuse in-memory checklist when already loaded (toggle/advance path).
    unloaded = sa_inspect(project).unloaded
    if "checklist_items" not in unloaded:
        proj_items = list(project.checklist_items or [])
    else:
        proj_items = list(
            (
                await db.execute(
                    select(ProjectChecklistItem).where(ProjectChecklistItem.project_id == project.id)
                )
            ).scalars().all()
        )
    done_keys = {(i.stage_number, i.item_key) for i in proj_items if i.is_done or i.stage_number < phase}

    for item in eng.checklist_items or []:
        if item.stage_number < DELIVERY_MIN_STAGE:
            continue
        should = (item.stage_number, item.item_key) in done_keys or item.stage_number < phase
        if should and not item.is_done:
            item.is_done = True
            item.done_at = datetime.now(timezone.utc)
        elif not should and item.stage_number > phase:
            item.is_done = False
            item.done_at = None


def checklist_progress(items: List[ProjectChecklistItem], stage: Optional[int] = None) -> int:
    scoped = [i for i in items if stage is None or i.stage_number == stage]
    if not scoped:
        return 0
    done = sum(1 for i in scoped if i.is_done)
    return int(round(100 * done / len(scoped)))


def phase_name(phase: int) -> str:
    meta = STAGES_BY_NUMBER.get(phase)
    return meta["name"] if meta else f"Phase {phase}"


async def link_engagement_handoff(
    db: AsyncSession,
    eng: Engagement,
    project: Project,
    *,
    enter_delivery: bool = True,
) -> None:
    """After sales handoff: link both ways, seed checklist, enter Requirement Analysis."""
    project.engagement_id = eng.id
    eng.project_id = project.id
    await ensure_project_checklist(db, project, done_through_stage=0)
    if enter_delivery:
        project.delivery_phase = max(project.delivery_phase or DELIVERY_MIN_STAGE, DELIVERY_MIN_STAGE)
        eng.current_stage = max(eng.current_stage, DELIVERY_MIN_STAGE)
    await sync_engagement_from_project(db, project)
