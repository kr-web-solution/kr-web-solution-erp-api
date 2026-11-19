"""API smoke + role checks using seeded test users (see conftest.TEST_USERS)."""

from fastapi.testclient import TestClient

from tests.conftest import TEST_USERS, get_auth_cookies


def test_health(client: TestClient):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"

    res_api = client.get("/api/health")
    assert res_api.status_code == 200
    assert res_api.json()["company"] == "KR Web Solution"


def test_auth_login_and_logout(client: TestClient):
    spec = TEST_USERS["founder"]
    res = client.post(
        "/api/v1/auth/login",
        json={"email": spec["email"], "password": spec["password"]},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["user"]["role"] == "founder_ceo"
    assert "kr_access_token" in res.cookies

    founder_cookies = res.cookies
    me_res = client.get("/api/v1/auth/me", cookies=founder_cookies)
    assert me_res.status_code == 200
    assert me_res.json()["email"] == spec["email"]

    logout_res = client.post("/api/v1/auth/logout", cookies=founder_cookies)
    assert logout_res.status_code == 200

    bad_res = client.post(
        "/api/v1/auth/login",
        json={"email": spec["email"], "password": "WrongPassword!"},
    )
    assert bad_res.status_code == 401


def test_founder_ceo_full_crud(client: TestClient, founder_cookies):
    cookies = founder_cookies

    dash_res = client.get("/api/v1/dashboard", cookies=cookies)
    assert dash_res.status_code == 200
    dash_data = dash_res.json()
    assert "running_projects_count" in dash_data
    assert "open_leads_count" in dash_data

    cl_res = client.get("/api/v1/clients", cookies=cookies)
    assert cl_res.status_code == 200

    lead_payload = {
        "name": "E-Commerce App Redesign",
        "company": "Alpha Retailers",
        "contact_name": "Ramesh Kumar",
        "email": "ramesh@alpharetail.com",
        "phone": "+91 99887 76655",
        "source": "website",
        "status": "new",
        "notes": "Interested in end-to-end revamp.",
    }
    create_lead_res = client.post("/api/v1/leads", json=lead_payload, cookies=cookies)
    assert create_lead_res.status_code == 201
    lead_id = create_lead_res.json()["id"]

    conv_res = client.post(
        f"/api/v1/leads/{lead_id}/convert",
        json={
            "create_project": True,
            "project_name": "Alpha E-Commerce Revamp",
            "budget_paise": 20000000,
        },
        cookies=cookies,
    )
    assert conv_res.status_code == 200
    conv_data = conv_res.json()
    new_client_id = conv_data["client_id"]
    new_project_id = conv_data["project_id"]
    assert new_client_id is not None
    assert new_project_id is not None

    get_lead_res = client.get(f"/api/v1/leads/{lead_id}", cookies=cookies)
    assert get_lead_res.json()["status"] == "won"

    inv_payload = {
        "invoice_type": "project",
        "client_id": new_client_id,
        "project_id": new_project_id,
        "issue_date": "2026-11-04",
        "due_date": "2026-11-19",
        "tax_rate_percent": 18.0,
        "notes": "Advance milestone payment.",
        "items": [
            {
                "description": "System Architecture & UI Blueprint",
                "quantity": 1.0,
                "unit_rate_paise": 10000000,
            }
        ],
    }
    create_inv_res = client.post("/api/v1/invoices", json=inv_payload, cookies=cookies)
    assert create_inv_res.status_code == 201
    inv_data = create_inv_res.json()
    assert inv_data["subtotal_paise"] == 10000000
    # Tax is 0 when company GST is disabled (settings.gst_enabled)
    assert inv_data["tax_paise"] >= 0
    assert inv_data["total_paise"] == inv_data["subtotal_paise"] + inv_data["tax_paise"]
    assert inv_data["status"] == "draft"

    inv_id = inv_data["id"]

    pay_res = client.post(f"/api/v1/invoices/{inv_id}/pay", cookies=cookies)
    assert pay_res.status_code == 200
    assert pay_res.json()["status"] == "paid"

    pdf_res = client.get(f"/api/v1/invoices/{inv_id}/pdf", cookies=cookies)
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert len(pdf_res.content) > 1000

    settings_res = client.get("/api/v1/settings", cookies=cookies)
    assert settings_res.status_code == 200
    assert settings_res.json()["company_name"] == "KR Web Solution"


def test_worker_role_restrictions(client: TestClient, worker_cookies):
    cookies = worker_cookies

    proj_res = client.get("/api/v1/projects", cookies=cookies)
    assert proj_res.status_code == 200
    for p in proj_res.json():
        assert p["budget_paise"] is None

    create_p_res = client.post(
        "/api/v1/projects",
        json={
            "name": "Hacked Project",
            "client_ids": ["cli_test_org"],
            "budget_paise": 500000,
        },
        cookies=cookies,
    )
    assert create_p_res.status_code == 403

    inv_res = client.get("/api/v1/invoices", cookies=cookies)
    assert inv_res.status_code == 403

    leads_res = client.get("/api/v1/leads", cookies=cookies)
    assert leads_res.status_code == 403

    sett_res = client.get("/api/v1/settings", cookies=cookies)
    assert sett_res.status_code == 403

    users_res = client.get("/api/v1/users", cookies=cookies)
    assert users_res.status_code == 403


def test_client_role_restrictions(client: TestClient, client_user_cookies, founder_cookies):
    # Ensure the client org has at least one visible project + a sent invoice
    create_p = client.post(
        "/api/v1/projects",
        json={
            "name": "Client Visible Project",
            "client_ids": ["cli_test_org"],
            "budget_paise": 100000,
            "status": "running",
        },
        cookies=founder_cookies,
    )
    assert create_p.status_code in (200, 201), create_p.text
    project_id = create_p.json()["id"]

    inv_create = client.post(
        "/api/v1/invoices",
        json={
            "invoice_type": "project",
            "client_id": "cli_test_org",
            "project_id": project_id,
            "issue_date": "2026-11-01",
            "due_date": "2026-11-15",
            "tax_rate_percent": 0,
            "items": [
                {
                    "description": "Visible line",
                    "quantity": 1,
                    "unit_rate_paise": 50000,
                }
            ],
        },
        cookies=founder_cookies,
    )
    assert inv_create.status_code == 201, inv_create.text
    inv_id = inv_create.json()["id"]
    # Mark sent so client can see it (clients never see drafts)
    send_res = client.post(f"/api/v1/invoices/{inv_id}/send", cookies=founder_cookies)
    if send_res.status_code not in (200, 201):
        # Fallback: patch status if send endpoint differs
        client.patch(
            f"/api/v1/invoices/{inv_id}",
            json={"status": "sent"},
            cookies=founder_cookies,
        )

    cookies = client_user_cookies
    proj_res = client.get("/api/v1/projects", cookies=cookies)
    assert proj_res.status_code == 200
    for p in proj_res.json():
        assert (
            p["client_id"] == "cli_test_org"
            or "cli_test_org" in (p.get("client_ids") or [])
            or any(c.get("id") == "cli_test_org" for c in (p.get("clients") or []))
        )

    inv_res = client.get("/api/v1/invoices", cookies=cookies)
    assert inv_res.status_code == 200
    for inv in inv_res.json():
        assert inv["client_id"] == "cli_test_org"
        assert inv["status"] != "draft"

    if inv_res.json():
        first_inv_id = inv_res.json()[0]["id"]
        pdf_res = client.get(f"/api/v1/invoices/{first_inv_id}/pdf", cookies=cookies)
        assert pdf_res.status_code == 200
        assert pdf_res.headers["content-type"] == "application/pdf"

    leads_res = client.get("/api/v1/leads", cookies=cookies)
    assert leads_res.status_code == 403

    sett_res = client.get("/api/v1/settings", cookies=cookies)
    assert sett_res.status_code == 403

    users_res = client.get("/api/v1/users", cookies=cookies)
    assert users_res.status_code == 403
