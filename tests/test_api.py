import pytest
from fastapi.testclient import TestClient

def get_auth_cookies(client: TestClient, email: str, password: str):
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, f"Login failed for {email}: {res.text}"
    return res.cookies

# ----------------- 1. Health Checks -----------------

def test_health(client: TestClient):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"

    res_api = client.get("/api/health")
    assert res_api.status_code == 200
    assert res_api.json()["company"] == "KR Web Solution"

# ----------------- 2. Auth & Cookie Flow -----------------

def test_auth_login_and_logout(client: TestClient):
    # Valid login as Founder
    res = client.post("/api/v1/auth/login", json={
        "email": "founder@krwebsolution.com",
        "password": "AdminKR@2026"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["user"]["role"] == "founder_ceo"
    assert "kr_access_token" in res.cookies

    founder_cookies = res.cookies

    # Verify /auth/me with cookie
    me_res = client.get("/api/v1/auth/me", cookies=founder_cookies)
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "founder@krwebsolution.com"

    # Logout
    logout_res = client.post("/api/v1/auth/logout", cookies=founder_cookies)
    assert logout_res.status_code == 200

    # Wrong credentials
    bad_res = client.post("/api/v1/auth/login", json={
        "email": "founder@krwebsolution.com",
        "password": "WrongPassword!"
    })
    assert bad_res.status_code == 401

# ----------------- 3. Founder CEO Full Access -----------------

def test_founder_ceo_full_crud(client: TestClient):
    cookies = get_auth_cookies(client, "founder@krwebsolution.com", "AdminKR@2026")

    # 1. Dashboard summary
    dash_res = client.get("/api/v1/dashboard", cookies=cookies)
    assert dash_res.status_code == 200
    dash_data = dash_res.json()
    assert "running_projects_count" in dash_data
    assert "open_leads_count" in dash_data

    # 2. Clients list
    cl_res = client.get("/api/v1/clients", cookies=cookies)
    assert cl_res.status_code == 200
    clients = cl_res.json()
    assert len(clients) > 0

    # 3. Create a test lead
    lead_payload = {
        "name": "E-Commerce App Redesign",
        "company": "Alpha Retailers",
        "contact_name": "Ramesh Kumar",
        "email": "ramesh@alpharetail.com",
        "phone": "+91 99887 76655",
        "source": "website",
        "status": "new",
        "notes": "Interested in end-to-end revamp."
    }
    create_lead_res = client.post("/api/v1/leads", json=lead_payload, cookies=cookies)
    assert create_lead_res.status_code == 201
    lead_id = create_lead_res.json()["id"]

    # 4. Convert lead to Client and Project
    conv_res = client.post(
        f"/api/v1/leads/{lead_id}/convert",
        json={"create_project": True, "project_name": "Alpha E-Commerce Revamp", "budget_paise": 20000000},
        cookies=cookies
    )
    assert conv_res.status_code == 200
    conv_data = conv_res.json()
    new_client_id = conv_data["client_id"]
    new_project_id = conv_data["project_id"]
    assert new_client_id is not None
    assert new_project_id is not None

    # Verify lead is now 'won'
    get_lead_res = client.get(f"/api/v1/leads/{lead_id}", cookies=cookies)
    assert get_lead_res.json()["status"] == "won"

    # 5. Create Invoice with atomic number and line items
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
                "unit_rate_paise": 10000000
            }
        ]
    }
    create_inv_res = client.post("/api/v1/invoices", json=inv_payload, cookies=cookies)
    assert create_inv_res.status_code == 201
    inv_data = create_inv_res.json()
    assert inv_data["subtotal_paise"] == 10000000
    assert inv_data["tax_paise"] == 1800000
    assert inv_data["total_paise"] == 11800000
    assert inv_data["status"] == "draft"

    inv_id = inv_data["id"]

    # Mark as paid
    pay_res = client.post(f"/api/v1/invoices/{inv_id}/pay", cookies=cookies)
    assert pay_res.status_code == 200
    assert pay_res.json()["status"] == "paid"

    # Download PDF
    pdf_res = client.get(f"/api/v1/invoices/{inv_id}/pdf", cookies=cookies)
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert len(pdf_res.content) > 1000

    # Settings
    settings_res = client.get("/api/v1/settings", cookies=cookies)
    assert settings_res.status_code == 200
    assert settings_res.json()["company_name"] == "KR Web Solution"

# ----------------- 4. Worker Role Permissions -----------------

def test_worker_role_restrictions(client: TestClient):
    cookies = get_auth_cookies(client, "worker@krwebsolution.com", "WorkerKR@2026")

    # 1. Projects: sees only assigned projects, budget_paise is hidden
    proj_res = client.get("/api/v1/projects", cookies=cookies)
    assert proj_res.status_code == 200
    projects = proj_res.json()
    for p in projects:
        assert p["budget_paise"] is None  # CRITICAL: hidden from worker

    # 2. Cannot create projects
    create_p_res = client.post("/api/v1/projects", json={
        "name": "Hacked Project", "client_id": "co001", "budget_paise": 500000
    }, cookies=cookies)
    assert create_p_res.status_code == 403

    # 3. Cannot view invoices
    inv_res = client.get("/api/v1/invoices", cookies=cookies)
    assert inv_res.status_code == 403

    # 4. Cannot view leads
    leads_res = client.get("/api/v1/leads", cookies=cookies)
    assert leads_res.status_code == 403

    # 5. Cannot view settings
    sett_res = client.get("/api/v1/settings", cookies=cookies)
    assert sett_res.status_code == 403

    # 6. Cannot view users
    users_res = client.get("/api/v1/users", cookies=cookies)
    assert users_res.status_code == 403

# ----------------- 5. Client Role Permissions -----------------

def test_client_role_restrictions(client: TestClient):
    cookies = get_auth_cookies(client, "client@asterretail.com", "ClientKR@2026")

    # 1. Projects: can only see their own projects
    proj_res = client.get("/api/v1/projects", cookies=cookies)
    assert proj_res.status_code == 200
    for p in proj_res.json():
        assert p["client_id"] == "co001"

    # 2. Invoices: can only see sent/paid invoices, NEVER draft
    inv_res = client.get("/api/v1/invoices", cookies=cookies)
    assert inv_res.status_code == 200
    for inv in inv_res.json():
        assert inv["client_id"] == "co001"
        assert inv["status"] != "draft"  # CRITICAL: client never sees drafts

    # 3. PDF download of their own invoice works
    if len(inv_res.json()) > 0:
        first_inv_id = inv_res.json()[0]["id"]
        pdf_res = client.get(f"/api/v1/invoices/{first_inv_id}/pdf", cookies=cookies)
        assert pdf_res.status_code == 200
        assert pdf_res.headers["content-type"] == "application/pdf"

    # 4. Cannot view leads
    leads_res = client.get("/api/v1/leads", cookies=cookies)
    assert leads_res.status_code == 403

    # 5. Cannot view settings
    sett_res = client.get("/api/v1/settings", cookies=cookies)
    assert sett_res.status_code == 403

    # 6. Cannot view users
    users_res = client.get("/api/v1/users", cookies=cookies)
    assert users_res.status_code == 403

