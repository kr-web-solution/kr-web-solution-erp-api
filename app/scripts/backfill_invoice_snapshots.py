"""Best-effort freeze for issued invoices missing document_snapshot.

Uses current settings/client at run time. Idempotent — never overwrites an
existing snapshot. Document that this is a one-time legacy heal, not a
rewrite of known historical identity.

Usage:
  python -m app.cli backfill-invoice-snapshots
  CONFIRM_REMOTE_WRITE=YES python -m app.cli backfill-invoice-snapshots --i-know-this-is-remote
"""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..core.db import AsyncSessionLocal
from ..models import Invoice, Settings
from ..services.invoice_snapshot import (
    ISSUED_SNAPSHOT_STATUSES,
    ensure_issued_snapshot,
    get_snapshot,
)


async def backfill_invoice_snapshots_async(*, dry_run: bool = False) -> Dict[str, Any]:
    async with AsyncSessionLocal() as db:
        st = (
            await db.execute(select(Settings).where(Settings.id == "default"))
        ).scalars().first()
        if not st:
            return {"ok": False, "error": "settings row missing", "frozen": 0, "skipped": 0}

        stmt = (
            select(Invoice)
            .options(
                selectinload(Invoice.client),
                selectinload(Invoice.project),
                selectinload(Invoice.parent_invoice),
                selectinload(Invoice.items),
            )
            .where(
                Invoice.status.in_(tuple(ISSUED_SNAPSHOT_STATUSES)),
                Invoice.deleted_at.is_(None),
            )
        )
        rows = (await db.execute(stmt)).scalars().unique().all()

        frozen = 0
        skipped = 0
        examples: list[str] = []
        for inv in rows:
            if get_snapshot(inv) is not None:
                skipped += 1
                continue
            if dry_run:
                frozen += 1
                if len(examples) < 10:
                    examples.append(inv.invoice_number)
                continue
            ensure_issued_snapshot(
                inv,
                client=inv.client,
                settings=st,
                project=inv.project,
                parent_invoice=inv.parent_invoice,
            )
            frozen += 1
            if len(examples) < 10:
                examples.append(inv.invoice_number)

        if not dry_run and frozen:
            await db.commit()

        return {
            "ok": True,
            "dry_run": dry_run,
            "examined": len(rows),
            "frozen": frozen,
            "skipped_already_frozen": skipped,
            "examples": examples,
            "note": (
                "Best-effort: freezes current settings/client onto issued rows "
                "that had no snapshot. Does not overwrite existing freezes."
            ),
        }
