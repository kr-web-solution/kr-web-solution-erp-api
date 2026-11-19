"""Prove test outbound never calls real SMTP; soft-deleted users cannot use tokens."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import aiosmtplib
from app.core.security import create_access_token
from app.models import Settings as SettingsModel
from app.services import email as email_service
from app.services.outbound import clear_outbox, get_outbox
from tests.conftest import TestingSessionLocal, TEST_USERS


@pytest.fixture(autouse=True)
def _clear_outbox_and_block_aiosmtp(monkeypatch):
    clear_outbox()

    async def _forbidden(*args, **kwargs):
        raise AssertionError("aiosmtplib.send must not be called during tests")

    monkeypatch.setattr(aiosmtplib, "send", _forbidden)
    yield
    clear_outbox()


@pytest.mark.asyncio
async def test_send_email_async_records_and_skips_smtp():
    async with TestingSessionLocal() as session:
        st = (
            await session.get(SettingsModel, "default")
        )
        assert st is not None
        ok = await email_service.send_email_async(
            to_email="nobody@example.com",
            subject="unit-test",
            body="hello",
            settings=st,
        )
    assert ok is True
    box = get_outbox()
    assert len(box) == 1
    assert box[0].channel == "email"
    assert box[0].to == "nobody@example.com"
    assert box[0].subject == "unit-test"


@pytest.mark.asyncio
async def test_soft_deleted_user_token_rejected(client: TestClient):
    from sqlalchemy import select
    from app.models import User
    from datetime import datetime, timezone

    async with TestingSessionLocal() as session:
        user = (
            await session.execute(
                select(User).where(User.email == TEST_USERS["worker"]["email"])
            )
        ).scalars().first()
        assert user is not None
        token = create_access_token(user.id, user.role)
        user.is_active = False
        user.deleted_at = datetime.now(timezone.utc)
        await session.commit()
        uid = user.id

    res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401, res.text

    # restore worker for other tests
    async with TestingSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == uid))
        ).scalars().first()
        user.is_active = True
        user.deleted_at = None
        await session.commit()
