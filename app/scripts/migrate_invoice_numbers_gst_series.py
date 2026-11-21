"""Renumber all invoices onto GST / non-GST series.

GST ON:  {COMPANY}-{CODE}-{YEAR}-{SEQ}     e.g. KRWS-SVC-2026-1001
GST OFF: {COMPANY}-{CODE}-{YEAR}-G-{SEQ}   e.g. KRWS-SVC-2026-G-1001

Each service × calendar year × GST series starts at 1001.
Also refreshes settings.invoice_sequences and parent refs in notes/snapshots.

Usage:
  python -m app.cli migrate-invoice-numbers --dry-run
  python -m app.cli migrate-invoice-numbers --apply
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from ..core.database import AsyncSessionLocal
from ..models import Invoice, Settings
from ..services.invoice_numbers import (
    DEFAULT_SEQ_START,
    company_code_from_settings,
    format_invoice_number,
    invoice_year_label,
    sequence_key,
    service_code_for,
)


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.today()


def resolve_invoice_gst(inv: Invoice, *, settings_gst: bool) -> bool:
    """Effective GST for numbering: override → tax evidence → settings."""
    override = getattr(inv, "gst_enabled", None)
    if override is not None:
        return bool(override)
    tax = int(getattr(inv, "tax_paise", 0) or 0)
    cgst = int(getattr(inv, "cgst_paise", 0) or 0)
    sgst = int(getattr(inv, "sgst_paise", 0) or 0)
    igst = int(getattr(inv, "igst_paise", 0) or 0)
    rate = float(getattr(inv, "tax_rate_percent", 0) or 0)
    if tax > 0 or cgst > 0 or sgst > 0 or igst > 0 or rate > 0:
        return True
    snap = getattr(inv, "document_snapshot", None) or {}
    if isinstance(snap, dict):
        company = snap.get("company") or {}
        if "gst_enabled" in company:
            return bool(company.get("gst_enabled"))
    return bool(settings_gst)


def _sort_key(inv: Invoice) -> Tuple:
    return (
        _as_date(inv.issue_date),
        inv.created_at or datetime.min,
        inv.invoice_number or "",
        inv.id or "",
    )


def _rewrite_text(text: Optional[str], mapping: Dict[str, str]) -> Optional[str]:
    if not text:
        return text
    out = text
    # Longest first so overlapping prefixes don't clobber
    for old in sorted(mapping.keys(), key=len, reverse=True):
        new = mapping[old]
        if old != new and old in out:
            out = out.replace(old, new)
    return out


async def migrate_invoice_numbers_gst_series_async(
    *,
    dry_run: bool = True,
    include_deleted: bool = False,
) -> Dict[str, Any]:
    """
    Renumber live invoices onto the GST / non-GST scheme.

    Soft-deleted rows are left alone unless they collide with a new number;
    collisions are parked as ``LEGACY-DEL-{id}`` so sequences stay clean.
    Pass include_deleted=True to force soft-deleted rows into the same series.
    """
    async with AsyncSessionLocal() as db:
        st = (
            await db.execute(select(Settings).where(Settings.id == "default"))
        ).scalars().first()
        if not st:
            return {"ok": False, "error": "settings row missing"}

        company = company_code_from_settings(st)
        settings_gst = bool(getattr(st, "gst_enabled", False))

        all_rows: List[Invoice] = list(
            (
                await db.execute(
                    select(Invoice).options(
                        selectinload(Invoice.items),
                        selectinload(Invoice.parent_invoice),
                    )
                )
            )
            .scalars()
            .unique()
            .all()
        )
        live = [inv for inv in all_rows if inv.deleted_at is None]
        deleted = [inv for inv in all_rows if inv.deleted_at is not None]
        invoices = all_rows if include_deleted else live

        if not invoices:
            return {
                "ok": True,
                "dry_run": dry_run,
                "examined": 0,
                "renamed": 0,
                "mapping": {},
                "sequences": {},
            }

        # Group → assign target numbers
        groups: Dict[Tuple[str, str, bool], List[Invoice]] = defaultdict(list)
        gst_resolved: Dict[str, bool] = {}
        for inv in invoices:
            gst_on = resolve_invoice_gst(inv, settings_gst=settings_gst)
            gst_resolved[inv.id] = gst_on
            code = service_code_for(inv.invoice_type)
            year = invoice_year_label(_as_date(inv.issue_date))
            groups[(code, year, gst_on)].append(inv)

        mapping: Dict[str, str] = {}
        plan: List[Dict[str, Any]] = []
        sequences: Dict[str, int] = {}

        for (code, year, gst_on), rows in sorted(groups.items(), key=lambda x: x[0]):
            rows_sorted = sorted(rows, key=_sort_key)
            seq = DEFAULT_SEQ_START
            for inv in rows_sorted:
                new_num = format_invoice_number(
                    company, code, year, seq, gst_enabled=gst_on
                )
                old = inv.invoice_number
                mapping[old] = new_num
                plan.append(
                    {
                        "id": inv.id,
                        "old": old,
                        "new": new_num,
                        "invoice_type": inv.invoice_type,
                        "gst": gst_on,
                        "year": year,
                        "code": code,
                        "seq": seq,
                        "deleted": inv.deleted_at is not None,
                    }
                )
                seq += 1
            sequences[sequence_key(code, year, gst_enabled=gst_on)] = seq

        # Soft-deleted that would collide with a new live number → park
        target_nums = {p["new"] for p in plan}
        park_plan: List[Dict[str, Any]] = []
        if not include_deleted:
            for inv in deleted:
                if inv.invoice_number in target_nums or inv.invoice_number in mapping:
                    parked = f"LEGACY-DEL-{inv.id}"
                    park_plan.append(
                        {
                            "id": inv.id,
                            "old": inv.invoice_number,
                            "new": parked,
                            "invoice_type": inv.invoice_type,
                            "gst": None,
                            "fy": None,
                            "code": None,
                            "seq": None,
                            "deleted": True,
                            "parked": True,
                        }
                    )

        unchanged = sum(1 for p in plan if p["old"] == p["new"])
        changed = [p for p in plan if p["old"] != p["new"]]

        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "examined": len(invoices),
                "renamed": len(changed),
                "unchanged": unchanged,
                "parked_deleted": len(park_plan),
                "company": company,
                "settings_gst": settings_gst,
                "sequences": sequences,
                "plan": plan + park_plan,
            }

        # Phase 1: park everyone we will touch on unique temps
        touch_ids = {p["id"] for p in plan} | {p["id"] for p in park_plan}
        by_id = {inv.id: inv for inv in all_rows}
        for inv_id in touch_ids:
            by_id[inv_id].invoice_number = f"__MIG__{inv_id}"
        await db.flush()

        # Phase 2: finals
        for p in park_plan:
            by_id[p["id"]].invoice_number = p["new"]

        for p in plan:
            inv = by_id[p["id"]]
            inv.invoice_number = p["new"]
            # Persist explicit GST so series stays stable if Settings toggle flips later
            inv.gst_enabled = bool(p["gst"])

        # Rewrite notes / line descriptions / snapshot parent refs (live + included)
        for inv in invoices:
            inv.notes = _rewrite_text(inv.notes, mapping)
            for item in inv.items or []:
                item.description = _rewrite_text(item.description, mapping) or item.description

            snap = getattr(inv, "document_snapshot", None)
            if isinstance(snap, dict):
                snap = dict(snap)
                parent_num = snap.get("parent_invoice_number")
                if parent_num and parent_num in mapping:
                    snap["parent_invoice_number"] = mapping[parent_num]
                company_block = dict(snap.get("company") or {})
                company_block["gst_enabled"] = bool(gst_resolved.get(inv.id, False))
                snap["company"] = company_block
                inv.document_snapshot = snap
                flag_modified(inv, "document_snapshot")

        st.invoice_sequences = sequences
        flag_modified(st, "invoice_sequences")
        st.invoice_next_number = max(
            [DEFAULT_SEQ_START] + [int(v) for v in sequences.values()]
        )

        await db.commit()

        return {
            "ok": True,
            "dry_run": False,
            "examined": len(invoices),
            "renamed": len(changed),
            "unchanged": unchanged,
            "parked_deleted": len(park_plan),
            "company": company,
            "settings_gst": settings_gst,
            "sequences": sequences,
            "plan": plan + park_plan,
        }
