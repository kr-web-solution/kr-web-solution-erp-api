"""Create Redberyl client, project, 3 paid invoices, maintenance plan + quotation.

Requires CONFIRM_REMOTE_DB=YES CONFIRM_DB_NAME=<db> --i-know-this-is-remote
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

# Ensure repo root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal, init_db  # noqa: E402
from app.core.db_target_guard import assert_migration_or_seed_allowed  # noqa: E402
from app.models import (  # noqa: E402
    Client,
    Invoice,
    InvoiceItem,
    Project,
    ProjectDocument,
    RecurringInvoice,
    Settings,
)
from app.services.client_project import create_project  # noqa: E402
from app.services.invoice_numbers import assign_unique_invoice_number  # noqa: E402
from app.services.invoice_typing import normalize_line_kind, resolve_invoice_typing  # noqa: E402
from app.services.project_files import save_project_upload  # noqa: E402


QUOTE_PATH = ROOT / "temp doc" / "KR_Web_Solution_Website Management_Quotation Redberyl Finance.pdf"


def _load_prod_url() -> str:
    text = (ROOT / ".env.production").read_text(encoding="utf-8")
    m = re.search(r"^DATABASE_URL=(.+)$", text, re.M)
    if not m:
        raise SystemExit("No DATABASE_URL in .env.production")
    return m.group(1).strip().strip('"').strip("'")


async def _create_paid_invoice(
    session,
    *,
    st: Settings,
    client_id: str,
    project_id: str,
    invoice_type: str,
    issue_date: date,
    items: list[dict],
    covers_from: date | None = None,
    covers_to: date | None = None,
    period_months: int = 1,
    notes: str | None = None,
    monthly_rate_paise: int | None = None,
) -> Invoice:
    header_type, ledger, number_hint = resolve_invoice_typing(
        invoice_type=invoice_type, items=items
    )
    subtotal = sum(int(round(float(i["quantity"]) * int(i["unit_rate_paise"]))) for i in items)
    paid_at = datetime.combine(issue_date, datetime.min.time(), tzinfo=timezone.utc)
    inv = Invoice(
        invoice_number="",
        invoice_type=header_type,
        ledger=ledger,
        client_id=client_id,
        project_id=project_id,
        status="paid",
        issue_date=issue_date,
        due_date=issue_date,
        subtotal_paise=subtotal,
        tax_rate_percent=0,
        tax_paise=0,
        total_paise=subtotal,
        notes=notes,
        period_months=period_months,
        covers_from=covers_from,
        covers_to=covers_to,
        monthly_rate_paise=monthly_rate_paise,
        paid_at=paid_at,
        email_status="sent",
    )
    await assign_unique_invoice_number(
        session,
        inv,
        invoice_type=number_hint,
        issue_date=issue_date,
        settings_row=st,
    )
    for i in items:
        amt = int(round(float(i["quantity"]) * int(i["unit_rate_paise"])))
        session.add(
            InvoiceItem(
                invoice_id=inv.id,
                description=i["description"],
                quantity=float(i["quantity"]),
                unit_rate_paise=int(i["unit_rate_paise"]),
                amount_paise=amt,
                line_kind=normalize_line_kind(
                    i.get("line_kind"),
                    header_type if header_type != "mixed" else "custom_dev",
                ),
            )
        )
    return inv


async def main() -> None:
    url = _load_prod_url()
    os.environ["DATABASE_URL"] = url
    settings.DATABASE_URL = url
    assert_migration_or_seed_allowed(url, tool="seed-redberyl")

    await init_db()
    async with AsyncSessionLocal() as session:
        # Idempotent: skip if client already exists
        existing = (
            await session.execute(
                select(Client).where(
                    Client.name == "Redberyl Investment Managers LLP",
                    Client.deleted_at.is_(None),
                )
            )
        ).scalars().first()
        if existing:
            print("client_already_exists id=", existing.id)
            return

        st = (
            await session.execute(
                select(Settings).where(Settings.id == "default").with_for_update()
            )
        ).scalar_one()

        client = Client(
            name="Redberyl Investment Managers LLP",
            email="contact@redberyl.in",
            phone="+91 97892 93087",
            website="https://redberyl.in/",
            address=(
                "Redberyl Investment Managers LLP\n"
                "No. 216/17-4, Lotus Nagar\n"
                "K M S Garden, Fairlands\n"
                "Salem, Tamil Nadu 636016, India"
            ),
            city="Salem",
            state="Tamil Nadu",
            pincode="636016",
            primary_contact_name="Rakshith R",
            designation="Analyst",
            contact_email="rakshith@redberyl.in",
            contact_phone="+91 93415 56666",
        )
        session.add(client)
        await session.flush()

        project = await create_project(
            session,
            name="Redberyl Investment Managers LLP Website and management",
            client_ids=[client.id],
            description="develop full backend for public website deploy and maintance",
            budget_paise=1_500_000,
            development_days=15,
            status="running",
            start_date=date(2026, 10, 14),
        )
        project.go_live_at = date(2026, 10, 22)
        # Monthly maintenance ₹3000 — send day 1, due within ~4 days (window 1–5)
        plan = RecurringInvoice(
            client_id=client.id,
            project_id=project.id,
            amount_paise=300_000,
            send_day=1,
            due_after_days=4,
            reminder_every_days=2,
            starts_on=date(2026, 12, 1),
            next_run_date=date(2026, 12, 1),
            day_of_month=1,
            active=True,
            tax_rate_percent=0,
            notes="Monthly website management — auto invoice on the 1st (due by ~5th)",
        )
        session.add(plan)
        await session.flush()

        if QUOTE_PATH.is_file():
            import io
            from fastapi import UploadFile

            upload = UploadFile(
                filename=QUOTE_PATH.name,
                file=io.BytesIO(QUOTE_PATH.read_bytes()),
            )
            stored, original, content_type, size = await save_project_upload(
                project.id, upload
            )
            session.add(
                ProjectDocument(
                    project_id=project.id,
                    title="Quotation — Website Management",
                    original_filename=original,
                    stored_filename=stored,
                    content_type=content_type or "application/pdf",
                    size_bytes=size,
                    doc_kind="quotation",
                    uploaded_by=None,
                )
            )
        else:
            print("WARNING: quotation PDF missing at", QUOTE_PATH)

        # 1) DEV ₹7500 — 14/10/2026 paid
        inv1 = await _create_paid_invoice(
            session,
            st=st,
            client_id=client.id,
            project_id=project.id,
            invoice_type="custom_dev",
            issue_date=date(2026, 10, 14),
            items=[
                {
                    "description": "Development advance — website & management",
                    "quantity": 1,
                    "unit_rate_paise": 750_000,
                    "line_kind": "custom_dev",
                }
            ],
            notes="1st development payment",
        )

        # 2) MIXED DEV ₹7500 + MNT prorata ₹1000 — 22/10/2026 paid
        inv2 = await _create_paid_invoice(
            session,
            st=st,
            client_id=client.id,
            project_id=project.id,
            invoice_type="mixed",
            issue_date=date(2026, 10, 22),
            items=[
                {
                    "description": "Final Payment for development",
                    "quantity": 1,
                    "unit_rate_paise": 750_000,
                    "line_kind": "custom_dev",
                },
                {
                    "description": "22/10/2026 to 30/10/2026 Custom Maintenance amount",
                    "quantity": 1,
                    "unit_rate_paise": 100_000,
                    "line_kind": "amc",
                },
            ],
            covers_from=date(2026, 10, 22),
            covers_to=date(2026, 10, 30),
            period_months=1,
            monthly_rate_paise=300_000,
            notes="Final development + Oct prorata maintenance",
        )

        # 3) MNT ₹3000 — 14/11/2026 paid (November)
        inv3 = await _create_paid_invoice(
            session,
            st=st,
            client_id=client.id,
            project_id=project.id,
            invoice_type="amc",
            issue_date=date(2026, 11, 14),
            items=[
                {
                    "description": "Monthly Maintenance Support (2026-11)",
                    "quantity": 1,
                    "unit_rate_paise": 300_000,
                    "line_kind": "amc",
                }
            ],
            covers_from=date(2026, 11, 1),
            covers_to=date(2026, 11, 30),
            period_months=1,
            monthly_rate_paise=300_000,
            notes="November maintenance",
        )

        await session.commit()
        print("created client=", client.id)
        print("created project=", project.id)
        print("invoices=", inv1.invoice_number, inv2.invoice_number, inv3.invoice_number)
        print("plan=", plan.id, "next_run=", plan.next_run_date, "send_day=", plan.send_day)


if __name__ == "__main__":
    if "--i-know-this-is-remote" not in sys.argv:
        sys.argv.append("--i-know-this-is-remote")
    asyncio.run(main())
