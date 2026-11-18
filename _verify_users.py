import asyncio
from sqlalchemy import text
from app.core.database import engine

async def m():
    async with engine.connect() as c:
        print("alembic", (await c.execute(text("select version_num from alembic_version"))).scalar())
        users = (
            await c.execute(
                text(
                    "select email, role, "
                    "password_set_at is not null as pwd, "
                    "deleted_at is not null as del "
                    "from users order by email"
                )
            )
        ).fetchall()
        for u in users:
            print(u)
    await engine.dispose()

asyncio.run(m())
