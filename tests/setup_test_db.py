import asyncio
import asyncpg
import os
import subprocess

async def reset_test_database():
    base_url = "postgresql://postgres:udhaya@localhost:5432/postgres"
    conn = await asyncpg.connect(base_url)
    try:
        # Terminate any open connections to test db
        await conn.execute("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = 'kr-web-solution-erp-test' AND pid <> pg_backend_pid();
        """)
        await conn.execute('DROP DATABASE IF EXISTS "kr-web-solution-erp-test"')
        await conn.execute('CREATE DATABASE "kr-web-solution-erp-test"')
        print("Recreated clean test database: kr-web-solution-erp-test")
    finally:
        await conn.close()

    # Run alembic upgrade head on test database
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql+asyncpg://postgres:udhaya@localhost:5432/kr-web-solution-erp-test"
    res = subprocess.run(["python", "-m", "alembic", "upgrade", "head"], env=env, capture_output=True, text=True)
    print("Alembic upgrade output:", res.stdout)
    if res.returncode != 0:
        print("Alembic upgrade error:", res.stderr)

if __name__ == "__main__":
    asyncio.run(reset_test_database())
