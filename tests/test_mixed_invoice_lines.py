"""Mixed development + maintenance lines on one invoice."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.services.invoice_typing import resolve_invoice_typing
from tests.conftest import make_project_with_client
from tests.test_client_project_rules import _make_client
from app.core.database import AsyncSessionLocal as TestingSessionLocal


def test_resolve_mixed_typing():
    t, ledger, hint = resolve_invoice_typing(
        invoice_type="custom_dev",
        items=[
            {"line_kind": "custom_dev", "description": "dev", "quantity": 1, "unit_rate_paise": 1},
            {"line_kind": "amc", "description": "mnt", "quantity": 1, "unit_rate_paise": 1},
        ],
    )
    assert t == "mixed"
    assert ledger == "mixed"
    assert hint == "mixed"


@pytest.mark.asyncio
async def test_create_mixed_invoice_api(client: TestClient, founder_cookies):
    tag = uuid4().hex[:8]
    async with TestingSessionLocal() as session:
        c = await _make_client(session, f"MixClient-{tag}")
        p = await make_project_with_client(
            session, name=f"MixProject-{tag}", client_id=c.id, status="running"
        )
        await session.commit()
        cid, pid = c.id, p.id

    res = client.post(
        "/api/v1/invoices",
        json={
            "invoice_type": "mixed",
            "client_id": cid,
            "project_id": pid,
            "issue_date": "2026-10-22",
            "due_date": "2026-10-22",
            "tax_rate_percent": 0,
            "covers_from": "2026-10-22",
            "covers_to": "2026-10-30",
            "status": "paid",
            "items": [
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
        },
        cookies=founder_cookies,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["invoice_type"] == "mixed"
    assert body["ledger"] == "mixed"
    assert body["status"] == "paid"
    assert body["total_paise"] == 850_000
    assert "-MIX-" in body["invoice_number"], body["invoice_number"]
    kinds = {it["line_kind"] for it in body["items"]}
    assert kinds == {"custom_dev", "amc"}
