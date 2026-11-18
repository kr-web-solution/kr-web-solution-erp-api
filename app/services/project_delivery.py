"""Helpers for project delivery phases, checklists, and engagement sync."""

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..common.workflow import (
    DELIVERY_MAX_STAGE,
    DELIVERY_MIN_STAGE,
    STAGES_BY_NUMBER,
    WARRANTY_DEFAULT_DAYS,
    build_project_delivery_checklist_seed,
)
from ..models import Engagement, EngagementChecklistItem, Project, ProjectChecklistItem


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


async def ensure_project_checklist(db: AsyncSession, project: Project, done_through_stage: int = 0) -> None:
    existing = (
        await db.execute(select(ProjectChecklistItem).where(ProjectChecklistItem.project_id == project.id).limit(1))
    ).scalars().first()
    if existing:
        return
    for item in seed_project_checklist(project.id, done_through_stage=done_through_stage):
        db.add(item)
    await db.flush()


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
    if project.delivery_phase >= 11:
        eng.status = "maintenance" if project.status != "completed" else "completed"
    elif eng.status not in ("lost", "on_hold", "completed"):
        eng.status = "active"

    # Mirror checklist done flags for stages <= delivery_phase on engagement items 06–11
    phase = project.delivery_phase
    proj_items = (
        await db.execute(select(ProjectChecklistItem).where(ProjectChecklistItem.project_id == project.id))
    ).scalars().all()
    done_keys = {(i.stage_number, i.item_key) for i in proj_items if i.is_done or i.stage_number < phase}

    for item in eng.checklist_items or []:
        if item.stage_number < 5:
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
    """After onboarding: link both ways, seed checklist, move to design/dev."""
    project.engagement_id = eng.id
    eng.project_id = project.id
    await ensure_project_checklist(db, project, done_through_stage=5 if enter_delivery else 0)
    if enter_delivery:
        project.delivery_phase = max(project.delivery_phase or DELIVERY_MIN_STAGE, 6)
        eng.current_stage = max(eng.current_stage, 6)
        # Mark stage 5 engagement checklist done
        for item in eng.checklist_items or []:
            if item.stage_number == 5:
                item.is_done = True
                item.done_at = datetime.now(timezone.utc)
    await sync_engagement_from_project(db, project)
