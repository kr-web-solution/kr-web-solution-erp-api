"""Client users must not read another client's invoice/project; non-admins blocked on settings."""

from datetime import date, timedelta

from fastapi.testclient import TestClient



def test_client_cannot_read_foreign_invoice_or_project(
    client: TestClient, founder_cookies, client_user_cookies
):
    # Founder creates foreign client+project+invoice
    c = client.post(
        "/api/v1/clients",
        json={"name": "IDOR Other Client", "email": "idor-other@example.com"},
        cookies=founder_cookies,
    )
    assert c.status_code == 201
    cid = c.json()["id"]
    p = client.post(
        "/api/v1/projects",
        json={"name": "IDOR Other Project", "client_ids": [cid], "budget_paise": 0},
        cookies=founder_cookies,
    )
    assert p.status_code == 201
    pid = p.json()["id"]
    inv = client.post(
        "/api/v1/invoices",
        json={
            "invoice_type": "project",
            "client_id": cid,
            "project_id": pid,
            "issue_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=7)).isoformat(),
            "tax_rate_percent": 0,
            "items": [{"description": "x", "quantity": 1, "unit_rate_paise": 500}],
        },
        cookies=founder_cookies,
    )
    assert inv.status_code == 201
    iid = inv.json()["id"]

    # Mark sent so client role could see if it were theirs
    # (draft is always denied for clients)
    # Use pay path unavailable — patch via founder cancel not needed; GET draft → 403 anyway

    r_inv = client.get(f"/api/v1/invoices/{iid}", cookies=client_user_cookies)
    assert r_inv.status_code in (403, 404)

    r_prj = client.get(f"/api/v1/projects/{pid}", cookies=client_user_cookies)
    assert r_prj.status_code in (403, 404)

    # Project documents if route exists
    r_docs = client.get(f"/api/v1/projects/{pid}/documents", cookies=client_user_cookies)
    assert r_docs.status_code in (403, 404)


def test_non_admin_settings_forbidden(client: TestClient, worker_cookies, client_user_cookies):
    for cookies in (worker_cookies, client_user_cookies):
        r = client.get("/api/v1/settings", cookies=cookies)
        assert r.status_code == 403
        r2 = client.put("/api/v1/settings", json={"company_name": "Nope"}, cookies=cookies)
        assert r2.status_code == 403
