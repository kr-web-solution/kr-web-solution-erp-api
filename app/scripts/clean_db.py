import asyncio
from sqlalchemy import text
from app.core.database import AsyncSessionLocal

async def purge_dummy_data():
    async with AsyncSessionLocal() as session:
        # Delete dependent tables first
        await session.execute(text("DELETE FROM engagement_checklist_items;"))
        await session.execute(text("DELETE FROM engagements;"))
        await session.execute(text("DELETE FROM late_fee_actions;"))
        await session.execute(text("DELETE FROM invoice_items;"))
        await session.execute(text("DELETE FROM invoices;"))
        await session.execute(text("DELETE FROM recurring_invoices;"))
        await session.execute(text("DELETE FROM project_workers;"))
        await session.execute(text("DELETE FROM projects;"))
        await session.execute(text("DELETE FROM notes;"))
        await session.execute(text("DELETE FROM leads;"))
        await session.execute(text("DELETE FROM clients;"))

        # Keep privileged accounts (admin + founder_ceo)
        await session.execute(text("DELETE FROM users WHERE role NOT IN ('admin', 'founder_ceo');"))

        # Reset invoice next number to 1001
        await session.execute(text("UPDATE settings SET invoice_next_number = 1001 WHERE id = 'default';"))

        await session.commit()
        print("All dummy data purged successfully from database!")

if __name__ == "__main__":
    asyncio.run(purge_dummy_data())

