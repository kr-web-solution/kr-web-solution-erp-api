"""Fix live draft invoices whose gst_enabled flag disagrees with the number series.

Only tip-of-series drafts (no children) are hard-deleted and recreated on the
correct series. Others are reported and left alone.

Usage:
  python -m app.cli fix-gst-series-mismatches --dry-run
  python -m app.cli fix-gst-series-mismatches --apply
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..core.database import AsyncSessionLocal
from ..models import Invoice, Settings
from ..services.gst_series_switch import switch_invoice_gst_series
from ..services.invoice_numbers import (
    can_change_gst_series,
    parse_series_key,
    resolve_gst_for_numbering,
)


async def fix_gst_series_mismatches_async(*, dry_run: bool = True) -> Dict[str, Any]:
    async with AsyncSessionLocal() as db:
        st = (
            await db.execute(select(Settings).where(Settings.id == "default"))
        ).scalars().first()
        if not st:
            return {"ok": False, "error": "settings row missing"}

        rows: List[Invoice] = list(
            (
                await db.execute(
                    select(Invoice)
                    .options(
                        selectinload(Invoice.items),
                        selectinload(Invoice.client),
                        selectinload(Invoice.project),
                    )
                    .where(Invoice.deleted_at.is_(None))
                )
            )
            .scalars()
            .unique()
            .all()
        )

        mismatches = []
        fixed = []
        skipped = []

        for inv in rows:
            parsed = parse_series_key(inv.invoice_number)
            if not parsed:
                continue
            effective = resolve_gst_for_numbering(inv, settings_row=st)
            number_gst = bool(parsed["gst_enabled"])
            if effective == number_gst:
                continue
            mismatches.append(
                {
                    "id": inv.id,
                    "number": inv.invoice_number,
                    "status": inv.status,
                    "flag_gst": effective,
                    "number_gst": number_gst,
                }
            )
            if not await can_change_gst_series(db, inv):
                skipped.append(
                    {
                        **mismatches[-1],
                        "reason": "not_unpaid_tip_or_has_children",
                    }
                )
                continue
            if dry_run:
                fixed.append({**mismatches[-1], "action": "would_switch"})
                continue
            old_id = inv.id
            old_number = inv.invoice_number
            # Target series = what the flag says
            new_inv = await switch_invoice_gst_series(
                db,
                inv,
                target_gst_enabled=effective,
                settings_row=st,
            )
            fixed.append(
                {
                    "old_id": old_id,
                    "old_number": old_number,
                    "new_id": new_inv.id,
                    "new_number": new_inv.invoice_number,
                    "gst": effective,
                }
            )

        if not dry_run and fixed:
            await db.commit()

        return {
            "ok": True,
            "dry_run": dry_run,
            "examined": len(rows),
            "mismatches": len(mismatches),
            "fixed": fixed,
            "skipped": skipped,
        }
