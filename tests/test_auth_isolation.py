"""Client IDOR + admin settings isolation (test DB only)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import TEST_USERS


def _forbidden(status_code: int) -> None:
    assert status_code in (403, 404), status_code


def _other_client_resources(client: TestClient, founder_cookies) -> dict:
    create_c = client.post(
        "/api/v1/clients",
        json={"name": "Isolation Other Org"},
        cookies=founder_cookies,
    )
    assert create_c.status_code == 201, create_c.text
    other_client_id = create_c.json()["id"]

    create_p = client.post(
        "/api/v1/projects",
        json={
            "name": "Isolation Other Project",
            "client_ids": [other_client_id],
            "budget_paise": 100000,
            "status": "running",
        },
        cookies=founder_cookies,
    )
    assert create_p.status_code in (200, 201), create_p.text
    project_id = create_p.json()["id"]

    inv = client.post(
        "/api/v1/invoices",
        json={
            "invoice_type": "project",
            "client_id": other_client_id,
            "project_id": project_id,
            "issue_date": "2026-11-01",
            "due_date": "2026-11-15",
            "tax_rate_percent": 0,
            "items": [
                {
                    "description": "Other client line",
                    "quantity": 1,
                    "unit_rate_paise": 25000,
                }
            ],
        },
        cookies=founder_cookies,
    )
    assert inv.status_code == 201, inv.text
    invoice_id = inv.json()["id"]
    patched = client.patch(
        f"/api/v1/invoices/{invoice_id}",
        json={"status": "sent"},
        cookies=founder_cookies,
    )
    assert patched.status_code == 200, patched.text

    upload = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("isolation.txt", b"secret-other-client", "text/plain")},
        data={"title": "Other client doc", "doc_kind": "other"},
        cookies=founder_cookies,
    )
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()["id"]

    return {
        "client_id": other_client_id,
        "project_id": project_id,
        "invoice_id": invoice_id,
        "doc_id": doc_id,
    }


def test_client_cannot_get_another_clients_invoice(
    client: TestClient, founder_cookies, client_user_cookies
):
    ids = _other_client_resources(client, founder_cookies)
    res = client.get(
        f"/api/v1/invoices/{ids['invoice_id']}", cookies=client_user_cookies
    )
    _forbidden(res.status_code)


def test_client_cannot_get_another_clients_project(
    client: TestClient, founder_cookies, client_user_cookies
):
    ids = _other_client_resources(client, founder_cookies)
    res = client.get(
        f"/api/v1/projects/{ids['project_id']}", cookies=client_user_cookies
    )
    _forbidden(res.status_code)


def test_client_cannot_get_another_clients_project_document(
    client: TestClient, founder_cookies, client_user_cookies
):
    ids = _other_client_resources(client, founder_cookies)
    listed = client.get(
        f"/api/v1/projects/{ids['project_id']}/documents",
        cookies=client_user_cookies,
    )
    _forbidden(listed.status_code)

    file_res = client.get(
        f"/api/v1/projects/{ids['project_id']}/documents/{ids['doc_id']}/file",
        cookies=client_user_cookies,
    )
    _forbidden(file_res.status_code)


def test_worker_forbidden_on_admin_settings(client: TestClient, worker_cookies):
    assert client.get("/api/v1/settings", cookies=worker_cookies).status_code == 403
    assert (
        client.put("/api/v1/settings", json={}, cookies=worker_cookies).status_code
        == 403
    )
    assert (
        client.get("/api/v1/settings/email-templates", cookies=worker_cookies).status_code
        == 403
    )


def test_client_forbidden_on_admin_settings(client: TestClient, client_user_cookies):
    assert client.get("/api/v1/settings", cookies=client_user_cookies).status_code == 403
    assert (
        client.put("/api/v1/settings", json={}, cookies=client_user_cookies).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/settings/email-templates", cookies=client_user_cookies
        ).status_code
        == 403
    )


def test_client_cannot_patch_another_clients_invoice(
    client: TestClient, founder_cookies, client_user_cookies
):
    ids = _other_client_resources(client, founder_cookies)
    res = client.patch(
        f"/api/v1/invoices/{ids['invoice_id']}",
        json={"notes": "hijack"},
        cookies=client_user_cookies,
    )
    assert res.status_code == 403


def test_worker_cannot_get_invoices(client: TestClient, worker_cookies):
    assert TEST_USERS["worker"]["email"]
    res = client.get("/api/v1/invoices", cookies=worker_cookies)
    assert res.status_code == 403


def test_client_cannot_download_another_clients_invoice_pdf(
    client: TestClient, founder_cookies, client_user_cookies
):
    ids = _other_client_resources(client, founder_cookies)
    res = client.get(
        f"/api/v1/invoices/{ids['invoice_id']}/pdf",
        cookies=client_user_cookies,
    )
    _forbidden(res.status_code)


def test_client_cannot_get_another_clients_engagement(
    client: TestClient, founder_cookies, client_user_cookies
):
    ids = _other_client_resources(client, founder_cookies)
    create = client.post(
        "/api/v1/engagements",
        json={
            "title": "Isolation Other Engagement",
            "company_name": "Isolation Other Org",
            "enquiry_source": "referral",
            "engagement_kind": "project",
            "client_id": ids["client_id"],
        },
        cookies=founder_cookies,
    )
    assert create.status_code == 201, create.text
    eng_id = create.json()["id"]

    res = client.get(f"/api/v1/engagements/{eng_id}", cookies=client_user_cookies)
    _forbidden(res.status_code)

    doc_list_or_file = client.get(
        f"/api/v1/engagements/{eng_id}/documents/doc_fake/file",
        cookies=client_user_cookies,
    )
    _forbidden(doc_list_or_file.status_code)
