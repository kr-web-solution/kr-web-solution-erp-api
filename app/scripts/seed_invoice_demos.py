"""
Seed realistic demo invoices (GST + non-GST series) for Demo Technologies.

- Hard-deletes previous [DEMO_SEED] invoices (not soft-delete)
- Leaves real clients (e.g. Redberyl) untouched
- Allocates numbers via the live GST / non-GST series (no fake 2099 years)

Usage:
  python -m app.cli seed-invoice-demos
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from ..core.database import AsyncSessionLocal
from ..models import Client, Invoice, InvoiceItem, InvoicePayment, Project, ProjectClient, Settings
from ..services.gst_series_switch import hard_delete_invoice_completely
from ..services.invoice_numbers import (
    DEFAULT_SEQ_START,
    assign_unique_invoice_number,
    parse_series_key,
    sequence_key,
)
from ..services.invoice_snapshot import freeze_document_snapshot
from ..services.invoice_terms import resolve_invoice_terms
from ..services.invoice_typing import normalize_line_kind, resolve_invoice_typing
from ..services.tax_payroll import (
    compute_tax_paise,
    normalize_state_code,
    split_gst_paise,
)

DEMO_CLIENT_ID = "cli_demo_invoice"
DEMO_PROJECT_ID = "prj_demo_invoice"
DEMO_MARKER = "[DEMO_SEED]"

# Specs without fixed numbers — allocated at seed time.
# gst=True → GST series; gst=False → …-G-… series (Bill of Supply).
DEMO_SPECS: List[Dict[str, Any]] = [
    # --- GST ON ---
    {
        "key": "dev_gst",
        "type": "custom_dev",
        "ledger": "development",
        "status": "sent",
        "gst": True,
        "desc": "Custom web application — Phase 1 delivery",
        "qty": 1,
        "rate": 1_50_000_00,
        "unit": "Nos",
        "hsn": "998314",
    },
    # MNT GST tip rule: 1001 unpaid (editable, no GST toggle/delete), 1002 paid (frozen)
    {
        "key": "amc_gst",
        "type": "amc",
        "ledger": "maintenance",
        "status": "sent",
        "gst": True,
        "desc": "Annual Maintenance Contract — Web + API support",
        "qty": 12,
        "rate": 8_000_00,
        "unit": "Months",
        "hsn": "998313",
        "covers": True,
    },
    {
        "key": "mnt_gst_paid",
        "type": "amc",
        "ledger": "maintenance",
        "status": "paid",
        "gst": True,
        "desc": "Monthly maintenance & monitoring — settled period",
        "qty": 1,
        "rate": 12_000_00,
        "unit": "Months",
        "hsn": "998313",
        "covers": True,
    },
    {
        "key": "mix_gst",
        "type": "mixed",
        "ledger": "mixed",
        "status": "sent",
        "gst": True,
        "lines": [
            {
                "description": "Feature sprint (development)",
                "qty": 40,
                "rate": 1_500_00,
                "unit": "Hours",
                "hsn": "998314",
                "kind": "custom_dev",
            },
            {
                "description": "AMC support hours",
                "qty": 10,
                "rate": 1_200_00,
                "unit": "Hours",
                "hsn": "998313",
                "kind": "amc",
            },
        ],
    },
    {
        "key": "adv_gst",
        "type": "advance",
        "ledger": "development",
        "status": "paid",
        "gst": True,
        "desc": "Advance payment — ERP module kickoff",
        "qty": 1,
        "rate": 75_000_00,
        "unit": "Nos",
        "hsn": "998314",
    },
    {
        "key": "bal_gst",
        "type": "balance",
        "ledger": "development",
        "status": "sent",
        "gst": True,
        "desc": "Balance payment — ERP module delivery",
        "qty": 1,
        "rate": 75_000_00,
        "unit": "Nos",
        "hsn": "998314",
    },
    {
        "key": "hst_gst",
        "type": "hosting",
        "ledger": "development",
        "status": "sent",
        "gst": True,
        "desc": "Cloud hosting — Production (annual)",
        "qty": 1,
        "rate": 36_000_00,
        "unit": "Year",
        "hsn": "998315",
    },
    {
        "key": "svc_gst",
        "type": "services",
        "ledger": "development",
        "status": "sent",
        "gst": True,
        "desc": "IT consulting — Architecture workshop (2 days)",
        "qty": 2,
        "rate": 25_000_00,
        "unit": "Days",
        "hsn": "998313",
    },
    {
        "key": "lic_gst",
        "type": "licensing",
        "ledger": "development",
        "status": "sent",
        "gst": True,
        "desc": "SaaS seat licenses — Annual",
        "qty": 10,
        "rate": 4_999_00,
        "unit": "Licenses",
        "hsn": "998314",
    },
    {
        "key": "late_gst",
        "type": "late_fee",
        "ledger": "development",
        "status": "overdue",
        "gst": True,
        "desc": "Late payment fee on overdue balance",
        "qty": 1,
        "rate": 5_000_00,
        "unit": "Nos",
        "hsn": "998313",
        "parent_key": "dev_gst",
    },
    {
        "key": "crn_gst",
        "type": "credit_note",
        "ledger": "development",
        "status": "paid",
        "gst": True,
        "desc": "Credit note — Scope reduction adjustment",
        "qty": 1,
        "rate": 20_000_00,
        "unit": "Nos",
        "hsn": "998314",
        "parent_key": "dev_gst",
    },
    # --- GST OFF (Bill of Supply / non-GST series) ---
    {
        "key": "dev_nongst",
        "type": "custom_dev",
        "ledger": "development",
        "status": "draft",
        "gst": False,
        "desc": "Website redesign package (non-GST)",
        "qty": 1,
        "rate": 45_000_00,
        "unit": "Nos",
    },
    # MNT non-GST: G-1001 paid (frozen), G-1002 unpaid tip (can enable GST → next GST seq)
    {
        "key": "amc_nongst_paid",
        "type": "amc",
        "ledger": "maintenance",
        "status": "paid",
        "gst": False,
        "desc": "Care plan — small business site (non-GST, settled)",
        "qty": 1,
        "rate": 4_500_00,
        "unit": "Months",
        "covers": True,
    },
    {
        "key": "amc_nongst_tip",
        "type": "amc",
        "ledger": "maintenance",
        "status": "sent",
        "gst": False,
        "desc": "Care plan — small business site (non-GST)",
        "qty": 1,
        "rate": 4_500_00,
        "unit": "Months",
        "covers": True,
    },
    {
        "key": "mix_nongst",
        "type": "mixed",
        "ledger": "mixed",
        "status": "draft",
        "gst": False,
        "lines": [
            {
                "description": "Landing page updates",
                "qty": 8,
                "rate": 1_200_00,
                "unit": "Hours",
                "kind": "custom_dev",
            },
            {
                "description": "Content upload support",
                "qty": 4,
                "rate": 800_00,
                "unit": "Hours",
                "kind": "amc",
            },
        ],
    },
]


async def _ensure_demo_party(session) -> tuple[Client, Project, Settings]:
    st = (await session.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
    if not st:
        st = Settings(id="default", company_name="KR Web Solution")
        session.add(st)
        await session.flush()

    # Company identity for valid GST invoices — do not flip the Settings GST master switch.
    st.default_tax_rate = float(st.default_tax_rate or 18) or 18
    st.company_state = st.company_state or "Tamil Nadu"
    st.company_state_code = st.company_state_code or "33"
    st.company_gstin = st.company_gstin or "33AAAAA0000A1Z5"
    st.company_pan = st.company_pan or "AAAAA0000A"
    st.bank_beneficiary_name = st.bank_beneficiary_name or "KR Web Solution"
    st.bank_name = st.bank_name or "HDFC Bank"
    st.bank_account_number = st.bank_account_number or "50200012345678"
    st.bank_ifsc = st.bank_ifsc or "HDFC0001234"
    st.bank_upi_id = st.bank_upi_id or "krwebsolution@hdfcbank"
    st.bank_paypal_id = getattr(st, "bank_paypal_id", None) or "paypal.me/krwebsolution"
    if not st.bank_wire_instructions:
        st.bank_wire_instructions = (
            "For NEFT / RTGS / IMPS use the beneficiary, account number and IFSC above.\n"
            "Mention the invoice number in the payment narration and share the UTR."
        )
    st.default_sac_code = st.default_sac_code or "998314"
    st.invoice_prefix = (st.invoice_prefix or "KRWS").strip() or "KRWS"

    client = (
        await session.execute(select(Client).where(Client.id == DEMO_CLIENT_ID))
    ).scalars().first()
    if not client:
        client = Client(
            id=DEMO_CLIENT_ID,
            name="Demo Technologies Pvt Ltd",
            email="accounts@demotech.example",
            phone="+91 422 400 1000",
            contact_email="priya@demotech.example",
            primary_contact_name="Priya Natarajan",
            designation="Director",
            address="42 Industrial Estate, Peelamedu",
            city="Coimbatore",
            state="Tamil Nadu",
            pincode="641004",
            gstin="33BBBBB0000B1Z5",
            pan="BBBBB0000B",
            state_code="33",
            notes=f"{DEMO_MARKER} Demo client for invoice samples",
        )
        session.add(client)
    else:
        client.deleted_at = None
        client.name = "Demo Technologies Pvt Ltd"
        client.email = "accounts@demotech.example"
        client.phone = "+91 422 400 1000"
        client.contact_email = "priya@demotech.example"
        client.primary_contact_name = "Priya Natarajan"
        client.designation = "Director"
        client.address = "42 Industrial Estate, Peelamedu"
        client.city = "Coimbatore"
        client.state = "Tamil Nadu"
        client.pincode = "641004"
        client.gstin = "33BBBBB0000B1Z5"
        client.pan = "BBBBB0000B"
        client.state_code = "33"

    project = (
        await session.execute(select(Project).where(Project.id == DEMO_PROJECT_ID))
    ).scalars().first()
    if not project:
        project = Project(
            id=DEMO_PROJECT_ID,
            name="Demo ERP & Cloud Suite",
            client_id=DEMO_CLIENT_ID,
            budget_paise=50_00_000_00,
            status="running",
            description=f"{DEMO_MARKER} Demo project for invoice type samples",
        )
        session.add(project)
        await session.flush()
        session.add(
            ProjectClient(
                project_id=DEMO_PROJECT_ID,
                client_id=DEMO_CLIENT_ID,
                is_primary=True,
            )
        )
    else:
        project.deleted_at = None
        project.client_id = DEMO_CLIENT_ID
        project.name = "Demo ERP & Cloud Suite"
        project.budget_paise = 50_00_000_00
        project.status = "running"
        link = (
            await session.execute(
                select(ProjectClient).where(
                    ProjectClient.project_id == DEMO_PROJECT_ID,
                    ProjectClient.client_id == DEMO_CLIENT_ID,
                )
            )
        ).scalars().first()
        if not link:
            session.add(
                ProjectClient(
                    project_id=DEMO_PROJECT_ID,
                    client_id=DEMO_CLIENT_ID,
                    is_primary=True,
                )
            )

    await session.flush()
    return client, project, st


async def _purge_old_demos(session) -> int:
    """Hard-delete demo-client invoices and any legacy DEMO_SEED / 2099 leftovers."""
    rows = (
        await session.execute(
            select(Invoice).options(
                selectinload(Invoice.items),
                selectinload(Invoice.payments),
                selectinload(Invoice.late_fee_actions),
            )
        )
    ).scalars().unique().all()

    targets = [
        inv
        for inv in rows
        if inv.client_id == DEMO_CLIENT_ID
        or (inv.notes or "").startswith(DEMO_MARKER)
        or "2099" in (inv.invoice_number or "")
    ]
    # Children (credit/late fee) before parents
    targets.sort(
        key=lambda i: (0 if i.parent_invoice_id else 1, i.invoice_number or "")
    )

    removed = 0
    for inv in targets:
        await hard_delete_invoice_completely(session, inv)
        removed += 1
    return removed


def _rebuild_sequences(settings_row: Settings, invoices: List[Invoice]) -> None:
    """Set invoice_sequences to max(live seq)+1 per series."""
    sequences: Dict[str, Any] = {}
    for inv in invoices:
        parsed = parse_series_key(inv.invoice_number)
        if not parsed:
            continue
        key = sequence_key(
            parsed["code"], parsed["year"], gst_enabled=bool(parsed["gst_enabled"])
        )
        nxt = int(parsed["seq"]) + 1
        sequences[key] = max(int(sequences.get(key) or DEFAULT_SEQ_START), nxt)
    settings_row.invoice_sequences = sequences
    flag_modified(settings_row, "invoice_sequences")
    settings_row.invoice_next_number = max(
        [DEFAULT_SEQ_START] + [int(v) for v in sequences.values()]
    )


async def seed_invoice_demos_async() -> dict:
    created = 0
    removed = 0
    numbers: List[str] = []

    async with AsyncSessionLocal() as session:
        client, project, st = await _ensure_demo_party(session)
        removed = await _purge_old_demos(session)

        # Rebuild counters from remaining (real) invoices before allocating demos
        remaining = (
            await session.execute(
                select(Invoice).where(Invoice.deleted_at.is_(None))
            )
        ).scalars().all()
        _rebuild_sequences(st, list(remaining))
        await session.flush()

        today = date.today()
        by_key: Dict[str, Invoice] = {}

        for spec in DEMO_SPECS:
            gst_on = bool(spec.get("gst", True))
            lines = spec.get("lines")
            if not lines:
                lines = [
                    {
                        "description": spec["desc"],
                        "qty": spec["qty"],
                        "rate": spec["rate"],
                        "unit": spec.get("unit"),
                        "hsn": spec.get("hsn"),
                        "kind": spec["type"] if spec["type"] != "mixed" else "custom_dev",
                    }
                ]

            subtotal = sum(int(round(float(l["qty"]) * int(l["rate"]))) for l in lines)
            if gst_on:
                # Per-invoice GST ON — compute tax even if Settings master toggle is off.
                tax_rate = float(st.default_tax_rate or 18) or 18.0
                tax_paise = compute_tax_paise(subtotal, tax_rate)
                supplier_code = normalize_state_code(
                    getattr(st, "company_state_code", None)
                ) or normalize_state_code(getattr(st, "company_gstin", None))
                customer_code = normalize_state_code(
                    getattr(client, "state_code", None)
                ) or normalize_state_code(getattr(client, "gstin", None))
                split = split_gst_paise(
                    tax_paise,
                    supplier_state_code=supplier_code,
                    customer_state_code=customer_code,
                    gst_enabled=True,
                )
                total_paise = subtotal + tax_paise
                place_of_supply = (getattr(client, "state", None) or "").strip() or None
                supply_type = split.get("supply_type")
                cgst = int(split.get("cgst_paise") or 0)
                sgst = int(split.get("sgst_paise") or 0)
                igst = int(split.get("igst_paise") or 0)
            else:
                tax_rate = 0.0
                tax_paise = 0
                total_paise = subtotal
                place_of_supply = None
                supply_type = None
                cgst = sgst = igst = 0

            status = spec["status"]
            issue = today - timedelta(days=7)
            due = today + timedelta(days=8)
            if status == "overdue":
                due = today - timedelta(days=3)

            parent_id = None
            parent_inv: Optional[Invoice] = None
            if spec.get("parent_key"):
                parent_inv = by_key.get(spec["parent_key"])
                if parent_inv:
                    parent_id = parent_inv.id

            header_type, header_ledger, _ = resolve_invoice_typing(
                invoice_type=spec["type"],
                items=[
                    {
                        "line_kind": ln.get("kind"),
                        "description": ln["description"],
                        "quantity": ln["qty"],
                        "unit_rate_paise": ln["rate"],
                    }
                    for ln in lines
                ],
            )

            inv = Invoice(
                invoice_number="",
                invoice_type=header_type,
                ledger=header_ledger or spec["ledger"],
                client_id=client.id,
                project_id=project.id,
                status=status,
                issue_date=issue,
                due_date=due,
                subtotal_paise=subtotal,
                tax_rate_percent=tax_rate,
                tax_paise=tax_paise,
                total_paise=total_paise,
                gst_enabled=gst_on,
                place_of_supply=place_of_supply,
                supply_type=supply_type,
                cgst_paise=cgst,
                sgst_paise=sgst,
                igst_paise=igst,
                discount_paise=0,
                round_off_paise=0,
                notes=f"{DEMO_MARKER} Sample {spec['type']} ({'GST' if gst_on else 'non-GST'})",
                parent_invoice_id=parent_id,
                email_status="pending",
            )
            if spec.get("covers"):
                inv.covers_from = date(today.year, today.month, 1)
                # end of month-ish for AMC samples
                inv.covers_to = date(today.year, 12, 31) if gst_on else date(
                    today.year, today.month, 28
                )
                inv.period_months = 12 if gst_on else 1
                inv.monthly_rate_paise = int(lines[0]["rate"])

            if status == "paid":
                inv.amount_paid_paise = total_paise
                inv.paid_at = datetime.now(timezone.utc)
            else:
                inv.amount_paid_paise = 0
                inv.paid_at = None

            await assign_unique_invoice_number(
                session,
                inv,
                invoice_type=header_type,
                issue_date=issue,
                settings_row=st,
                gst_enabled=gst_on,
            )

            for line in lines:
                amt = int(round(float(line["qty"]) * int(line["rate"])))
                session.add(
                    InvoiceItem(
                        invoice_id=inv.id,
                        description=line["description"],
                        quantity=float(line["qty"]),
                        unit_rate_paise=int(line["rate"]),
                        amount_paise=amt,
                        line_kind=normalize_line_kind(
                            line.get("kind"),
                            header_type if header_type != "mixed" else "custom_dev",
                        ),
                        hsn_sac=(None if not gst_on else line.get("hsn") or st.default_sac_code),
                        unit=line.get("unit"),
                    )
                )

            if status == "paid":
                session.add(
                    InvoicePayment(
                        invoice_id=inv.id,
                        amount_paise=total_paise,
                        method="upi",
                        reference=f"UTR{inv.invoice_number[-4:]}DEMO",
                        paid_at=inv.paid_at,
                        note="Demo settlement",
                    )
                )

            await session.flush()

            # Freeze only paid — unpaid demos stay live (current company/client).
            if status == "paid":
                freeze_document_snapshot(
                    inv,
                    client=client,
                    settings=st,
                    project=project,
                    parent_invoice=parent_inv,
                    force=True,
                )
                terms = resolve_invoice_terms(
                    invoice_type=inv.invoice_type,
                    invoice_notes=None,
                    settings=st,
                    status=inv.status,
                )
                snap = dict(inv.document_snapshot or {})
                snap["terms"] = terms
                company = dict(snap.get("company") or {})
                company["gst_enabled"] = gst_on
                if not gst_on:
                    company["gstin"] = None
                    company["pan"] = None
                snap["company"] = company
                inv.document_snapshot = snap
                flag_modified(inv, "document_snapshot")

            by_key[spec["key"]] = inv
            numbers.append(inv.invoice_number)
            created += 1

        # Final sequence rebuild including new demos
        all_live = (
            await session.execute(
                select(Invoice).where(Invoice.deleted_at.is_(None))
            )
        ).scalars().all()
        _rebuild_sequences(st, list(all_live))

        await session.commit()

    return {
        "removed_old_demos": removed,
        "created": created,
        "numbers": numbers,
    }
