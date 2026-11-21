"""Client ↔ Project ↔ Invoice / MaintenancePlan business rules.

All mutating helpers run inside the caller's transaction (no commit here).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import not_deleted
from ..core.exceptions import BusinessRuleError, ConflictException, NotFoundError
from ..models import Client, Invoice, Project, ProjectClient, RecurringInvoice


# ---------------------------------------------------------------------------
# Membership helpers
# ---------------------------------------------------------------------------

def normalize_client_ids(
    *,
    client_ids: Optional[Sequence[str]] = None,
    client_id: Optional[str] = None,
) -> List[str]:
    """Merge client_ids + legacy client_id; require at least one unique id."""
    ordered: List[str] = []
    for raw in list(client_ids or []) + ([client_id] if client_id else []):
        cid = (raw or "").strip()
        if cid and cid not in ordered:
            ordered.append(cid)
    if not ordered:
        raise BusinessRuleError("A project must have at least one client")
    return ordered


async def assert_clients_exist(db: AsyncSession, client_ids: Iterable[str]) -> List[Client]:
    ids = list(client_ids)
    res = await db.execute(select(Client).where(Client.id.in_(ids), not_deleted(Client)))
    found = list(res.scalars().all())
    found_ids = {c.id for c in found}
    missing = [cid for cid in ids if cid not in found_ids]
    if missing:
        raise NotFoundError(f"Client not found: {missing[0]}")
    inactive = [c for c in found if getattr(c, "is_active", True) is False]
    if inactive:
        raise BusinessRuleError(
            f"Client '{inactive[0].name}' is deactivated and cannot be linked to new work"
        )
    return found


async def get_project_or_404(
    db: AsyncSession,
    project_id: str,
    *,
    for_update: bool = False,
) -> Project:
    stmt = select(Project).where(Project.id == project_id, not_deleted(Project))
    if for_update:
        stmt = stmt.with_for_update()
    project = (await db.execute(stmt)).scalars().first()
    if not project:
        raise NotFoundError("Project not found")
    return project


async def assert_client_on_project(
    db: AsyncSession,
    project_id: str,
    client_id: str,
) -> None:
    row = (
        await db.execute(
            select(ProjectClient).where(
                ProjectClient.project_id == project_id,
                ProjectClient.client_id == client_id,
            )
        )
    ).scalars().first()
    if not row:
        raise BusinessRuleError("Client is not part of this project")


def project_has_client(project: Project, client_id: Optional[str]) -> bool:
    """Membership check via project_clients (loaded `clients`) — not the legacy column alone."""
    if not client_id:
        return False
    clients = getattr(project, "clients", None) or []
    if any(c.id == client_id for c in clients):
        return True
    # Fallback only when M2M not loaded
    return project.client_id == client_id


def primary_client_id_from_project(project: Project) -> Optional[str]:
    """Release-1 read path: prefer project_clients.is_primary; sync column is fallback.

    When `project.clients` is loaded as Client objects without is_primary, callers
    should pass membership rows via ``primary_client_id_from_links``.
    """
    return getattr(project, "client_id", None)


async def resolve_primary_client_id(db: AsyncSession, project_id: str) -> Optional[str]:
    """Authoritative primary from project_clients (release-1; column kept in sync)."""
    row = (
        await db.execute(
            select(ProjectClient).where(
                ProjectClient.project_id == project_id,
                ProjectClient.is_primary.is_(True),
            )
        )
    ).scalars().first()
    if row:
        return row.client_id
    any_row = (
        await db.execute(
            select(ProjectClient).where(ProjectClient.project_id == project_id)
        )
    ).scalars().first()
    return any_row.client_id if any_row else None


async def sync_project_client_id_column(db: AsyncSession, project: Project) -> None:
    """Keep projects.client_id mirrored from primary project_clients row (until drop)."""
    primary = await resolve_primary_client_id(db, project.id)
    if primary and project.client_id != primary:
        project.client_id = primary
        await db.flush()


# ---------------------------------------------------------------------------
# Create / membership
# ---------------------------------------------------------------------------

async def set_project_clients(
    db: AsyncSession,
    project: Project,
    client_ids: Sequence[str],
    *,
    primary_client_id: Optional[str] = None,
) -> None:
    """Replace membership. Enforces ≥1 client. Syncs project.client_id (primary)."""
    ids = normalize_client_ids(client_ids=client_ids)
    await assert_clients_exist(db, ids)

    primary = primary_client_id or ids[0]
    if primary not in ids:
        raise BusinessRuleError("Primary client must be one of the project clients")

    await db.execute(delete(ProjectClient).where(ProjectClient.project_id == project.id))
    for cid in ids:
        db.add(
            ProjectClient(
                project_id=project.id,
                client_id=cid,
                is_primary=(cid == primary),
            )
        )
    project.client_id = primary  # kept in sync until column drop (F-005)
    await db.flush()


async def create_project(
    db: AsyncSession,
    *,
    name: str,
    client_ids: Sequence[str],
    client_id: Optional[str] = None,
    description: Optional[str] = None,
    budget_paise: int = 0,
    development_days: Optional[int] = None,
    status: str = "lead",
    **extra: Any,
) -> Project:
    """Create a project with ≥1 client in one transaction (caller commits)."""
    ids = normalize_client_ids(client_ids=client_ids, client_id=client_id)
    await assert_clients_exist(db, ids)
    primary = ids[0]

    project = Project(
        name=name,
        client_id=primary,
        description=description,
        budget_paise=budget_paise or 0,
        development_days=development_days,
        status=status,
        delivery_phase=extra.get("delivery_phase", 5),
        start_date=extra.get("start_date"),
        end_date=extra.get("end_date"),
    )
    db.add(project)
    await db.flush()
    await set_project_clients(db, project, ids, primary_client_id=primary)
    return project


async def add_client_to_project(
    db: AsyncSession,
    project_id: str,
    client_id: str,
) -> Project:
    """Link client to project. Idempotent if already linked."""
    project = await get_project_or_404(db, project_id, for_update=True)
    cid = (client_id or "").strip()
    if not cid:
        raise BusinessRuleError("client_id is required")
    await assert_clients_exist(db, [cid])

    existing = (
        await db.execute(
            select(ProjectClient).where(
                ProjectClient.project_id == project.id,
                ProjectClient.client_id == cid,
            )
        )
    ).scalars().first()
    if existing:
        return project  # idempotent no-op

    db.add(
        ProjectClient(
            project_id=project.id,
            client_id=cid,
            is_primary=False if project.client_id else True,
        )
    )
    if not project.client_id:
        project.client_id = cid
    await db.flush()
    return project


# Backwards-compatible aliases used by older routers
async def add_project_client(db: AsyncSession, project: Project, client_id: str) -> None:
    await add_client_to_project(db, project.id, client_id)


async def remove_client_from_project(
    db: AsyncSession,
    project_id: str,
    client_id: str,
) -> Project:
    """Remove a client. Blocks if it would leave the project with 0 clients.

    Locks the project row with SELECT … FOR UPDATE to avoid concurrent removals
    leaving zero clients.
    """
    project = await get_project_or_404(db, project_id, for_update=True)
    cid = (client_id or "").strip()

    rows = (
        await db.execute(
            select(ProjectClient)
            .where(ProjectClient.project_id == project.id)
            .with_for_update()
        )
    ).scalars().all()
    current_ids = [r.client_id for r in rows]

    if cid not in current_ids:
        raise NotFoundError("Client is not on this project")
    if len(current_ids) <= 1:
        raise BusinessRuleError("Cannot remove the last client from a project")

    await db.execute(
        delete(ProjectClient).where(
            ProjectClient.project_id == project.id,
            ProjectClient.client_id == cid,
        )
    )
    if project.client_id == cid:
        remaining = [x for x in current_ids if x != cid]
        new_primary = remaining[0]
        project.client_id = new_primary
        await db.execute(
            text(
                """
                UPDATE project_clients
                SET is_primary = (client_id = :primary)
                WHERE project_id = :project_id
                """
            ),
            {"primary": new_primary, "project_id": project.id},
        )
    await db.flush()
    return project


async def remove_project_client(db: AsyncSession, project: Project, client_id: str) -> None:
    await remove_client_from_project(db, project.id, client_id)


# ---------------------------------------------------------------------------
# Delete client / project
# ---------------------------------------------------------------------------

async def sole_client_projects(
    db: AsyncSession,
    client_id: str,
) -> List[Tuple[str, str]]:
    """Projects where this client is the only linked client. Returns (id, name)."""
    # Single query: projects linked to this client having COUNT(*) = 1
    stmt = text(
        """
        SELECT p.id, p.name
        FROM projects p
        WHERE p.deleted_at IS NULL
          AND EXISTS (
              SELECT 1 FROM project_clients pc
              WHERE pc.project_id = p.id AND pc.client_id = :client_id
          )
          AND (
              SELECT COUNT(*) FROM project_clients pc2
              WHERE pc2.project_id = p.id
          ) = 1
        ORDER BY p.name
        """
    )
    rows = (await db.execute(stmt, {"client_id": client_id})).all()
    return [(r[0], r[1]) for r in rows]


async def delete_client(db: AsyncSession, client_id: str) -> Client:
    """Soft-delete a client after clearing non-sole project_clients links.

    Blocks if the client is the sole client of any project.
    """
    client = (
        await db.execute(select(Client).where(Client.id == client_id, not_deleted(Client)))
    ).scalars().first()
    if not client:
        raise NotFoundError("Client not found")

    sole = await sole_client_projects(db, client_id)
    if sole:
        names = ", ".join(n for _, n in sole[:5])
        more = f" (+{len(sole) - 5} more)" if len(sole) > 5 else ""
        raise ConflictException(
            f"Cannot delete client: they are the only client on: {names}{more}",
            project_ids=[pid for pid, _ in sole],
            project_names=[n for _, n in sole],
        )

    await db.execute(delete(ProjectClient).where(ProjectClient.client_id == client_id))
    client.soft_delete()
    await db.flush()
    return client


async def count_project_children(db: AsyncSession, project_id: str) -> Dict[str, int]:
    invoice_count = (
        await db.scalar(
            select(func.count())
            .select_from(Invoice)
            .where(Invoice.project_id == project_id, not_deleted(Invoice))
        )
        or 0
    )
    plan_count = (
        await db.scalar(
            select(func.count())
            .select_from(RecurringInvoice)
            .where(RecurringInvoice.project_id == project_id, not_deleted(RecurringInvoice))
        )
        or 0
    )
    return {"invoice_count": int(invoice_count), "plan_count": int(plan_count)}


async def delete_project(db: AsyncSession, project_id: str) -> Project:
    """Soft-delete a project; cascades soft-delete of its recurring maintenance plans.

    Invoices are kept (AR history) — delete is blocked while live invoices remain.
    """
    project = await get_project_or_404(db, project_id, for_update=True)
    counts = await count_project_children(db, project_id)
    inv_n = counts["invoice_count"]
    if inv_n > 0:
        raise ConflictException(
            f"Cannot delete project. Delete its {inv_n} invoice(s) first.",
            invoice_count=inv_n,
            plan_count=counts["plan_count"],
        )

    # Cascade: remove maintenance plans with the project
    plans = (
        await db.execute(
            select(RecurringInvoice).where(
                RecurringInvoice.project_id == project_id,
                not_deleted(RecurringInvoice),
            )
        )
    ).scalars().all()
    for plan in plans:
        plan.active = False
        plan.soft_delete()

    project.soft_delete()
    await db.flush()
    return project


# ---------------------------------------------------------------------------
# Invoice / maintenance plan creation guards
# ---------------------------------------------------------------------------

async def require_project_for_billing(
    db: AsyncSession,
    project_id: Optional[str],
    *,
    client_id: Optional[str] = None,
) -> Project:
    """Rule 6 + optional rule 8: project required; optional client membership check."""
    if not project_id:
        raise BusinessRuleError("project_id is required")
    project = await get_project_or_404(db, project_id)
    if client_id:
        await assert_client_on_project(db, project_id, client_id)
    return project


async def create_invoice_guards(
    db: AsyncSession,
    *,
    project_id: Optional[str],
    client_id: str,
) -> Project:
    """Validate project + client membership before invoice insert."""
    return await require_project_for_billing(db, project_id, client_id=client_id)


async def create_maintenance_plan(
    db: AsyncSession,
    *,
    project_id: str,
    client_id: str,
    amount_paise: int = 0,
    send_day: int = 1,
    due_after_days: int = 5,
    reminder_every_days: int = 2,
    starts_on: Optional[date] = None,
    ends_on: Optional[date] = None,
    line_items_template: Optional[List[dict]] = None,
    tax_rate_percent: float = 0.0,
    day_of_month: Optional[int] = None,
    next_run_date: Optional[date] = None,
    active: bool = True,
    notes: Optional[str] = None,
) -> RecurringInvoice:
    """Create a recurring maintenance plan bound to a project."""
    await require_project_for_billing(db, project_id, client_id=client_id)
    await assert_clients_exist(db, [client_id])

    send = min(max(1, send_day), 28)
    starts = starts_on or date.today()
    next_run = next_run_date or date(2099, 1, 1)
    plan = RecurringInvoice(
        client_id=client_id,
        project_id=project_id,
        amount_paise=amount_paise or 0,
        send_day=send,
        due_after_days=due_after_days or 5,
        reminder_every_days=reminder_every_days or 2,
        starts_on=starts,
        ends_on=ends_on,
        line_items_template=line_items_template or [],
        tax_rate_percent=tax_rate_percent or 0.0,
        day_of_month=day_of_month or send,
        next_run_date=next_run,
        active=active,
        notes=notes,
    )
    db.add(plan)
    await db.flush()
    return plan
