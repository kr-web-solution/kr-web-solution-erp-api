"""F-039: audit_logs rows for invoice, project, settings, login, membership."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal as TestingSessionLocal
from app.models import AuditLog
from tests.conftest import TEST_USERS


def _audit_rows(client: TestClient, cookies, **params):
    res = client.get("/api/v1/audit-logs", params=params, cookies=cookies)
    assert res.status_code == 200, res.text
    return res.json()


def test_login_success_and_failure_write_audit(client: TestClient):
    spec = TEST_USERS["founder"]
    fail = client.post(
        "/api/v1/auth/login",
        json={"email": spec["email"], "password": "WrongPassword!"},
        headers={"user-agent": "audit-test-agent"},
    )
    assert fail.status_code == 401

    ok = client.post(
        "/api/v1/auth/login",
        json={"email": spec["email"], "password": spec["password"]},
        headers={"user-agent": "audit-test-agent"},
    )
    assert ok.status_code == 200
    cookies = ok.cookies

    success = _audit_rows(client, cookies, entity_type="auth", action="login_success")
    assert any(r["action"] == "login_success" for r in success)

    failure = _audit_rows(client, cookies, entity_type="auth", action="login_failure")
    assert any(r["action"] == "login_failure" for r in failure)


def test_invoice_create_writes_audit(client: TestClient, founder_cookies):
    tag = uuid4().hex[:8]
    c = client.post(
        "/api/v1/clients",
        json={"name": f"AudInv Client {tag}", "email": f"aud-inv-{tag}@example.com"},
        cookies=founder_cookies,
    )
    assert c.status_code == 201, c.text
    cid = c.json()["id"]
    p = client.post(
        "/api/v1/projects",
        json={"name": f"AudInv Project {tag}", "client_ids": [cid], "budget_paise": 0},
        cookies=founder_cookies,
    )
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    inv = client.post(
        "/api/v1/invoices",
        json={
            "invoice_type": "project",
            "client_id": cid,
            "project_id": pid,
            "issue_date": "2026-11-19",
            "due_date": "2026-12-03",
            "items": [{"description": "Line", "quantity": 1.0, "unit_rate_paise": 1000}],
        },
        cookies=founder_cookies,
    )
    assert inv.status_code == 201, inv.text
    inv_id = inv.json()["id"]
    rows = _audit_rows(client, founder_cookies, entity_type="invoices", entity_id=inv_id)
    assert any(r["action"] == "create" and r["entity_id"] == inv_id for r in rows)
    assert any(r.get("actor_id") for r in rows)


def test_project_create_and_update_write_audit(client: TestClient, founder_cookies):
    tag = uuid4().hex[:8]
    c = client.post(
        "/api/v1/clients",
        json={"name": f"AudPrj Client {tag}", "email": f"aud-prj-{tag}@example.com"},
        cookies=founder_cookies,
    )
    assert c.status_code == 201, c.text
    cid = c.json()["id"]
    p = client.post(
        "/api/v1/projects",
        json={"name": f"AudPrj {tag}", "client_ids": [cid], "budget_paise": 1000},
        cookies=founder_cookies,
    )
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    patch = client.patch(
        f"/api/v1/projects/{pid}",
        json={"name": f"AudPrj {tag} renamed"},
        cookies=founder_cookies,
    )
    assert patch.status_code == 200, patch.text
    rows = _audit_rows(client, founder_cookies, entity_type="projects", entity_id=pid)
    actions = {r["action"] for r in rows}
    assert "create" in actions
    assert "update" in actions


def test_settings_update_writes_audit(client: TestClient, founder_cookies):
    res = client.put(
        "/api/v1/settings",
        json={"company_name": "KR Web Solution"},
        cookies=founder_cookies,
    )
    assert res.status_code == 200, res.text
    rows = _audit_rows(client, founder_cookies, entity_type="settings", entity_id="default")
    assert any(r["action"] == "update" for r in rows)


def test_membership_add_writes_audit(client: TestClient, founder_cookies):
    tag = uuid4().hex[:8]
    c1 = client.post(
        "/api/v1/clients",
        json={"name": f"AudMem A {tag}", "email": f"aud-mem-a-{tag}@example.com"},
        cookies=founder_cookies,
    )
    c2 = client.post(
        "/api/v1/clients",
        json={"name": f"AudMem B {tag}", "email": f"aud-mem-b-{tag}@example.com"},
        cookies=founder_cookies,
    )
    assert c1.status_code == 201 and c2.status_code == 201
    a_id, b_id = c1.json()["id"], c2.json()["id"]
    p = client.post(
        "/api/v1/projects",
        json={"name": f"AudMem Project {tag}", "client_ids": [a_id], "budget_paise": 0},
        cookies=founder_cookies,
    )
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    add = client.post(
        f"/api/v1/projects/{pid}/clients",
        json={"client_id": b_id},
        cookies=founder_cookies,
    )
    assert add.status_code == 200, add.text
    rows = _audit_rows(client, founder_cookies, entity_type="project_clients", entity_id=pid)
    assert any(r["action"] == "membership_add" for r in rows)


@pytest.mark.asyncio
async def test_scheduler_invoice_audit_actor_null():
    """Direct helper: scheduler writes create with actor_id null and note=scheduler."""
    from app.services.audit import write_audit

    async with TestingSessionLocal() as session:
        row = await write_audit(
            session,
            None,
            "invoices",
            "inv_sched_note",
            "create",
            after={"note": "scheduler"},
            note="scheduler",
        )
        await session.commit()
        assert row is not None
        stored = (
            await session.execute(select(AuditLog).where(AuditLog.id == row.id))
        ).scalars().first()
        assert stored is not None
        assert stored.actor_id is None
        assert stored.action == "create"
        assert (stored.after or {}).get("note") == "scheduler"
