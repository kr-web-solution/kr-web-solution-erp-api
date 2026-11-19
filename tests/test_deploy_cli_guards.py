"""Production lifespan must not seed; CLI bootstrap/seed guards."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.db_target_guard import assert_migration_or_seed_allowed
from app.scripts import seed as seed_mod


def test_lifespan_source_does_not_call_seed():
    import main as main_mod

    src = inspect.getsource(main_mod.lifespan)
    assert "from app.scripts.seed" not in src
    assert "seed_database_async" not in src
    assert "await seed" not in src
    assert "init_db" in src
    assert "log_scheduler_startup_status" in src


def test_seed_refuses_non_local_db():
    with pytest.raises(SystemExit) as ei:
        assert_migration_or_seed_allowed(
            "postgresql+asyncpg://u:p@203.0.113.10:5432/prod_db",
            tool="seed",
        )
    assert "REFUSING seed" in str(ei.value)


@pytest.mark.asyncio
async def test_bootstrap_admin_idempotent_and_touches_no_other_users():
    other = SimpleNamespace(
        id="usr_other",
        email="other@example.com",
        role="worker",
        deleted_at=None,
        password_hash="keep-me",
        full_name="Other",
        is_active=True,
    )

    session = AsyncMock()
    # First call: admin exists count = 1
    count_result = MagicMock()
    count_result.scalar_one.return_value = 1
    session.execute = AsyncMock(return_value=count_result)

    with patch.object(seed_mod, "ensure_roles_seeded", new=AsyncMock()):
        created = await seed_mod.bootstrap_admin_if_missing(session)

    assert created is False
    session.add.assert_not_called()
    # Must not mutate the other user object
    assert other.password_hash == "keep-me"
    assert other.full_name == "Other"


@pytest.mark.asyncio
async def test_bootstrap_admin_skips_when_email_taken_by_non_admin():
    """Never modifies an existing user even if SUPER_ADMIN email matches."""
    existing = SimpleNamespace(
        id="usr_x",
        email="boss@example.com",
        role="worker",
        password_hash="unchanged",
        full_name="Worker",
        deleted_at=None,
    )

    session = AsyncMock()

    count_result = MagicMock()
    count_result.scalar_one.return_value = 0  # no admin

    existing_result = MagicMock()
    existing_result.scalars.return_value.first.return_value = existing

    session.execute = AsyncMock(side_effect=[count_result, existing_result])

    with (
        patch.object(seed_mod, "ensure_roles_seeded", new=AsyncMock()),
        patch.object(seed_mod.settings, "SUPER_ADMIN_EMAIL", "boss@example.com"),
        patch.object(seed_mod.settings, "SUPER_ADMIN_PASSWORD", "Secret123!"),
        patch.object(seed_mod.settings, "SUPER_ADMIN_NAME", "Boss"),
        patch.object(seed_mod.settings, "FIRST_SUPERUSER_EMAIL", None),
        patch.object(seed_mod.settings, "FIRST_SUPERUSER_PASSWORD", None),
        patch.object(seed_mod.settings, "FIRST_SUPERUSER_NAME", None),
    ):
        created = await seed_mod.bootstrap_admin_if_missing(session)

    assert created is False
    session.add.assert_not_called()
    assert existing.password_hash == "unchanged"
    assert existing.full_name == "Worker"
