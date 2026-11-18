import pytest
from datetime import date
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.models import Client, Project, RecurringInvoice, Invoice, LateFeeAction, Settings
from tests.conftest import TestingSessionLocal

def test_step1_schema_extensions(client):
    """Test model extensions and uniqueness constraints on PostgreSQL."""
    import asyncio

    async def run_db_tests():
        async with TestingSessionLocal() as session:
            # 1. Create client
            client_rec = Client(
                id="clt_test_maint",
                name="Maintenance Client",
                email="maint@client.com",
                primary_contact_name="Contact Person"
            )
            session.add(client_rec)
            await session.flush()

            # 2. Create RecurringInvoice with all extended columns
            rec = RecurringInvoice(
                id="rcr_test_01",
                client_id=client_rec.id,
                amount_paise=1500000,
                send_day=5,
                due_after_days=5,
                reminder_every_days=2,
                starts_on=date(2026, 11, 1),
                ends_on=date(2027, 11, 1),
                next_run_date=date(2026, 11, 5),
                active=True
            )
            session.add(rec)
            await session.flush()

            # 3. Create Invoice with period_key linked to recurring
            inv1 = Invoice(
                invoice_number="INV-2026-TEST-01",
                invoice_type="maintenance",
                client_id=client_rec.id,
                recurring_id=rec.id,
                period_key="2026-11",
                issue_date=date(2026, 11, 5),
                due_date=date(2026, 11, 10),
                subtotal_paise=1500000,
                tax_rate_percent=18.0,
                tax_paise=270000,
                total_paise=1770000,
                email_status="pending",
                email_retry_count=0
            )
            session.add(inv1)
            await session.flush()

            # 4. Create LateFeeAction
            lfa = LateFeeAction(
                invoice_id=inv1.id,
                fee_paise=50000,
                fee_type="fixed",
                fee_value=500.0,
                reason="Payment not received by due date",
                send_on=date(2026, 11, 13),
                status="pending"
            )
            session.add(lfa)
            await session.flush()

            # 5. Check duplicate (recurring_id, period_key) raises IntegrityError
            inv_dup = Invoice(
                invoice_number="INV-2026-TEST-DUP",
                invoice_type="maintenance",
                client_id=client_rec.id,
                recurring_id=rec.id,
                period_key="2026-11",
                issue_date=date(2026, 11, 5),
                due_date=date(2026, 11, 10),
                subtotal_paise=1500000,
                tax_rate_percent=18.0,
                tax_paise=270000,
                total_paise=1770000
            )
            session.add(inv_dup)
            with pytest.raises(IntegrityError):
                await session.flush()

            await session.rollback()

    asyncio.run(run_db_tests())

