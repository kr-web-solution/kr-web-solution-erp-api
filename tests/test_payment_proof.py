"""Client payment proof upload → founder verify-and-paid."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient


def _tiny_png_bytes() -> bytes:
    # Minimal valid 1x1 PNG
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf"
        b"\xc0\x00\x00\x00\x03\x00\x01\x00\x05\xfe\xd4\xef\x00\x00\x00\x00IEND\xaeB`\x82"
    )

def _create_sent_invoice(client: TestClient, founder_cookies) -> str:
    tag = uuid4().hex[:6]
    create_p = client.post(
        "/api/v1/projects",
        json={
            "name": f"Proof Project {tag}",
            "client_ids": ["cli_test_org"],
            "budget_paise": 100_000,
            "status": "running",
        },
        cookies=founder_cookies,
    )
    assert create_p.status_code in (200, 201), create_p.text
    project_id = create_p.json()["id"]

    inv_create = client.post(
        "/api/v1/invoices",
        json={
            "invoice_type": "custom_dev",
            "client_id": "cli_test_org",
            "project_id": project_id,
            "issue_date": "2026-11-01",
            "due_date": "2026-11-15",
            "tax_rate_percent": 0,
            "status": "sent",
            "items": [
                {
                    "description": "Website work",
                    "quantity": 1,
                    "unit_rate_paise": 50_000,
                }
            ],
        },
        cookies=founder_cookies,
    )
    assert inv_create.status_code == 201, inv_create.text
    body = inv_create.json()
    if body["status"] == "draft":
        patched = client.patch(
            f"/api/v1/invoices/{body['id']}",
            json={"status": "sent"},
            cookies=founder_cookies,
        )
        assert patched.status_code == 200, patched.text
    return body["id"]


def test_client_upload_then_founder_verify(
    client: TestClient, founder_cookies, client_user_cookies
):
    inv_id = _create_sent_invoice(client, founder_cookies)

    # Verify without proof → 400
    bad = client.post(f"/api/v1/invoices/{inv_id}/verify-payment", cookies=founder_cookies)
    assert bad.status_code == 400, bad.text

    png = _tiny_png_bytes()
    up = client.post(
        f"/api/v1/invoices/{inv_id}/payment-proof",
        files={"file": ("upi-proof.png", png, "image/png")},
        data={"note": "UTR 123456"},
        cookies=client_user_cookies,
    )
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["status"] == "proof_pending"
    assert body["has_payment_proof"] is True
    assert body["payment_proof_filename"] == "upi-proof.png"
    assert body["payment_proof_note"] == "UTR 123456"

    dl = client.get(f"/api/v1/invoices/{inv_id}/payment-proof", cookies=founder_cookies)
    assert dl.status_code == 200
    assert dl.content[:8] == b"\x89PNG\r\n\x1a\n"

    verified = client.post(
        f"/api/v1/invoices/{inv_id}/verify-payment",
        cookies=founder_cookies,
    )
    assert verified.status_code == 200, verified.text
    paid = verified.json()
    assert paid["status"] == "paid"
    assert paid["paid_at"] is not None
    assert paid["payment_verified_at"] is not None
    assert paid["has_payment_proof"] is True


def test_worker_cannot_upload_payment_proof(
    client: TestClient, founder_cookies, worker_cookies
):
    inv_id = _create_sent_invoice(client, founder_cookies)
    png = _tiny_png_bytes()
    res = client.post(
        f"/api/v1/invoices/{inv_id}/payment-proof",
        files={"file": ("x.png", png, "image/png")},
        cookies=worker_cookies,
    )
    assert res.status_code == 403
